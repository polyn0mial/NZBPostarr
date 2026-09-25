"""HTTP submission of an NZB to one indexer.

Plans the request, sends it (Cloudflare retry), and parses success/duplicate/failure into a SubmitResult.
"""

import re
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from loguru import logger

from core.redaction import redact_mapping, redact_text, redact_url
from core.utils import log_success, log_verbose
from core.indexers.categories import _resolve_category, _resolve_movie_submission_key
from core.indexers.models import (
    CategoryMapping,
    IndexerDefinition,
    SubmitResult,
    _requires_username,
)
from core.indexers.models import resolve_indexer_api_key, resolve_indexer_username

_FileSource = Path | bytes
_FileSpec = Tuple[str, str, _FileSource, str]


@dataclass(frozen=True, slots=True)
class _SubmissionRequest:
    """Fully planned HTTP request, separate from network and response effects."""

    method: str
    url: str
    params: Dict[str, str]
    data: Dict[str, str]
    headers: Dict[str, str]
    file_specs: Tuple[_FileSpec, ...]
    is_curl: bool
    submission_filename: str


def _check_success(
    indexer: IndexerDefinition, response: requests.Response
) -> tuple[bool, bool]:
    """
    Check if submission was successful.
    Returns (success, is_duplicate).
    """
    text = response.text
    text_upper = text.upper()
    text_lower = text.lower()

    # OMG can include generic success-looking words in duplicate/error pages.
    # Treat duplicate text as a non-accepted state unless a later bypass attempt
    # returns a clean success response.
    # Check duplicate patterns first
    for pattern in indexer.success.duplicate_patterns:
        if pattern.lower() in text_lower:
            return True, True

    # Check JSON response if configured
    if indexer.success.json_path:
        try:
            data = response.json()
            # Navigate the JSON path
            parts = indexer.success.json_path.split(".")
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, {})
                else:
                    value = None
                    break

            # If json_value is set, check for exact match
            if indexer.success.json_value:
                if str(value).upper() == indexer.success.json_value.upper():
                    return True, False
            # Otherwise, just check if the value is truthy (exists and not empty)
            elif value:
                return True, False
        except (TypeError, ValueError):
            pass

    # Check text patterns - use word-boundary matching for short patterns
    # to avoid false positives (e.g. "OK" matching inside "TOKEN" or "BROKEN").
    for pattern in indexer.success.text_patterns:
        pat_upper = pattern.upper()
        if len(pat_upper) <= 3:
            # Short patterns: require word-boundary match
            if re.search(
                r"(?<![A-Z])" + re.escape(pat_upper) + r"(?![A-Z])", text_upper
            ):
                return True, False
        else:
            if pat_upper in text_upper:
                return True, False

    return False, False


def _add_category_and_name(
    fields: Dict[str, str],
    indexer: IndexerDefinition,
    category: Optional[str],
    rls_name: str,
) -> None:
    if category:
        fields[indexer.category_param] = category
    if indexer.name_param:
        fields[indexer.name_param] = rls_name


def _build_submission_fields(
    indexer: IndexerDefinition,
    rls_name: str,
    api_key: Optional[str],
    username: Optional[str],
    category: Optional[str],
    *,
    is_curl: bool,
) -> tuple[Dict[str, str], Dict[str, str]]:
    params: Dict[str, str] = {}
    data: Dict[str, str] = dict(indexer.extra_form_data)

    if indexer.auth.method == "query_param" and api_key:
        params[indexer.auth.api_key_param] = api_key
        if username and indexer.auth.username_param:
            params[indexer.auth.username_param] = username

    if is_curl:
        if category:
            data["catid"] = category
        data.update(upload="upload", rlsname=rls_name.replace(",", "."))
    else:
        is_newznab = (
            indexer.profile == "newznab"
            or "t" in indexer.extra_params
            or indexer.submit_url.endswith("/api")
        )
        if is_newznab:
            params["t"] = indexer.extra_params.get("t", "nzbadd")
            if api_key:
                params[indexer.auth.api_key_param] = api_key
            _add_category_and_name(params, indexer, category, rls_name)
            _add_category_and_name(data, indexer, category, rls_name)
        elif indexer.method.upper() == "POST":
            _add_category_and_name(data, indexer, category, rls_name)
        else:
            _add_category_and_name(params, indexer, category, rls_name)

        if indexer.auth.method == "form_field" and api_key:
            data[indexer.auth.api_key_param] = api_key

    params.update(indexer.extra_params)
    return params, data


def _build_submission_headers(
    indexer: IndexerDefinition, api_key: Optional[str]
) -> Dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    if indexer.auth.method == "header" and api_key:
        if indexer.auth.header_template:
            headers[indexer.auth.header_name or "Authorization"] = (
                indexer.auth.header_template.format(api_key=api_key)
            )
        else:
            headers[indexer.auth.header_name or "Authorization"] = f"Bearer {api_key}"
    if indexer.auth.method == "header":
        headers["Accept"] = "application/json"
    return headers


def _build_submission_file_specs(
    indexer: IndexerDefinition,
    nzb_path: Path,
    submission_filename: str,
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
    *,
    is_curl: bool,
) -> Tuple[_FileSpec, ...]:
    file_specs: List[_FileSpec] = [
        (indexer.files.nzb, submission_filename, nzb_path, indexer.files.nzb_mime),
    ]
    if indexer.files.nfo or is_curl:
        nfo_field = indexer.files.nfo or "nfo"
        if nfo_path and nfo_path.exists():
            file_specs.append((nfo_field, nfo_path.name, nfo_path, "text/plain"))
        elif mediainfo_path and mediainfo_path.exists():
            file_specs.append(
                (nfo_field, "mediainfo.nfo", mediainfo_path, "text/plain")
            )
        elif indexer.requires_nfo or is_curl:
            file_specs.append((nfo_field, f"{indexer.id}_empty.nfo", b"", "text/plain"))
    if indexer.files.mediainfo and mediainfo_path and mediainfo_path.exists():
        file_specs.append(
            (indexer.files.mediainfo, mediainfo_path.name, mediainfo_path, "text/plain")
        )
    return tuple(file_specs)


def _build_submission_request(
    *,
    indexer: IndexerDefinition,
    rls_name: str,
    nzb_path: Path,
    api_key: Optional[str],
    username: Optional[str],
    category: Optional[str],
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
) -> _SubmissionRequest:
    """Plan request metadata and file sources without opening files or performing I/O."""
    is_curl = indexer.method.upper() == "CURL"
    submit_url = indexer.submit_url
    if is_curl and indexer.curl_template:
        submit_url = indexer.curl_template.format(
            api_key=api_key or "",
            username=username or "",
            user=username or "",
            api=api_key or "",
        )

    submission_filename = f"{rls_name}.nzb"
    params, data = _build_submission_fields(
        indexer,
        rls_name,
        api_key,
        username,
        category,
        is_curl=is_curl,
    )

    method = (
        "POST"
        if is_curl or indexer.method.upper() == "POST"
        else indexer.method.upper()
    )
    return _SubmissionRequest(
        method=method,
        url=submit_url,
        params=params,
        data=data,
        headers=_build_submission_headers(indexer, api_key),
        file_specs=_build_submission_file_specs(
            indexer,
            nzb_path,
            submission_filename,
            nfo_path,
            mediainfo_path,
            is_curl=is_curl,
        ),
        is_curl=is_curl,
        submission_filename=submission_filename,
    )


@contextmanager
def _open_multipart_files(
    file_specs: Tuple[_FileSpec, ...],
) -> Iterator[Dict[str, tuple]]:
    """Open request streams for exactly one attempt and close every handle together."""
    with ExitStack() as stack:
        payload: Dict[str, tuple] = {}
        for field_name, filename, source, mime in file_specs:
            stream = (
                stack.enter_context(source.open("rb"))
                if isinstance(source, Path)
                else source
            )
            payload[field_name] = (filename, stream, mime)
        yield payload


def _estimate_payload_size(file_specs: Tuple[_FileSpec, ...]) -> int:
    total = 0
    for _field_name, _filename, source, _mime in file_specs:
        if isinstance(source, Path):
            try:
                total += int(source.stat().st_size)
            except OSError:
                continue
        else:
            total += len(source)
    return total


def _request_with_cloudflare_retry(
    indexer: IndexerDefinition,
    submission: _SubmissionRequest,
) -> Optional[requests.Response]:
    """Perform a submission, rebuilding consumed streams for each bounded retry."""
    max_retries = 3
    backoff_seconds = 15
    response: Optional[requests.Response] = None

    for attempt in range(max_retries):
        with _open_multipart_files(submission.file_specs) as files_payload:
            response = requests.request(
                method=submission.method,
                url=submission.url,
                params=submission.params or None,
                headers=submission.headers or None,
                data=submission.data or None,
                files=files_payload,
                timeout=indexer.timeout,
                verify=not submission.is_curl,
            )

        is_cloudflare_challenge = (
            response.status_code == 403
            and "just a moment" in response.text[:500].lower()
        )
        if not is_cloudflare_challenge:
            break

        if attempt < max_retries - 1:
            wait = backoff_seconds * (2**attempt)
            logger.info(
                f"{indexer.log_name} Cloudflare challenge "
                f"(attempt {attempt + 1}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        logger.warning(
            f"{indexer.log_name} Cloudflare blocked after {max_retries} attempts"
        )
        break

    return response


def _duplicate_bypass_name(rls_name: str) -> str:
    if "." not in rls_name:
        return f"{rls_name}.."
    return "..".join(rls_name.rsplit(".", 1))


def submit_to_indexer(
    indexer: IndexerDefinition,
    rls_name: str,
    nzb_path: Path,
    config: Any,
    cat: str = "tv",
    nfo_path: Optional[Path] = None,
    mediainfo_path: Optional[Path] = None,
    attempt_suffix: str = "",
) -> SubmitResult:
    """
    Submit an NZB to an indexer using its definition.
    Returns (ok, status, reason) where status conveys failure/success class.
    """

    # Resolve API key (YAML first, then config/env)
    api_key = resolve_indexer_api_key(indexer, config)

    if not api_key and indexer.auth.method != "none":
        reason = f"Missing required API key for indexer '{indexer.id}'"
        logger.warning(reason)
        return SubmitResult(False, "misconfigured", reason)

    # Resolve username if needed (YAML first, then config/env)
    username = resolve_indexer_username(indexer, config)
    if _requires_username(indexer) and not username:
        reason = f"Missing required username for indexer '{indexer.id}'"
        logger.warning(reason)
        return SubmitResult(False, "misconfigured", reason)

    # Prepare category
    requested_submission_key = _resolve_movie_submission_key(cat, rls_name)
    category, matched_key, direct_match = _resolve_category(
        indexer, requested_submission_key
    )
    uses_submission_category = indexer.categories.uses_submission_category()
    requested_category = CategoryMapping.normalize_key(requested_submission_key)

    if uses_submission_category and not category:
        reason = (
            f"Category mapping mismatch for indexer '{indexer.id}': "
            f"detected '{requested_submission_key}' has no valid category_id"
        )
        logger.error(reason)
        return SubmitResult(False, "misconfigured", reason)

    if uses_submission_category:
        logger.info(
            f"[CATEGORY] {(requested_category or cat).upper()} -> Indexer: {indexer.id} -> Category ID: {category}"
        )
        logger.info(
            f"Detected: {(requested_category or cat).upper()} -> category_id: {category} -> Final: {category}"
        )
        if matched_key and not direct_match:
            logger.warning(
                f"{indexer.log_name} category '{requested_category or cat}' not defined explicitly; "
                f"using '{matched_key}' mapping instead"
            )
    else:
        logger.info(
            f"[CATEGORY] {(requested_category or cat).upper()} -> Indexer: {indexer.id} -> Category ID: [not used]"
        )

    submission = _build_submission_request(
        indexer=indexer,
        rls_name=rls_name,
        nzb_path=nzb_path,
        api_key=api_key,
        username=username,
        category=category,
        nfo_path=nfo_path,
        mediainfo_path=mediainfo_path,
    )

    # Standard HTTP submission
    try:
        log_verbose(
            f"Submitting to {indexer.log_name}: "
            f"{submission.submission_filename} (Cat: {category}){attempt_suffix}"
        )
        response = _request_with_cloudflare_retry(indexer, submission)

        if response is None:
            logger.error(f"{indexer.log_name} Failed to get any response from indexer.")
            return SubmitResult(False, "network_error", "No response from indexer")

        response.raise_for_status()

        success, is_duplicate = _check_success(indexer, response)

        if is_duplicate:
            # DUPLICATE BYPASS LOGIC
            if (
                getattr(config, "enable_duplicate_bypass", False)
                and "Duplicate-Bypass" not in attempt_suffix
            ):
                new_rls_name = _duplicate_bypass_name(rls_name)
                logger.info(
                    f"{indexer.log_name} Duplicate detected. Retrying with tweaked name: {new_rls_name}"
                )
                return submit_to_indexer(
                    indexer=indexer,
                    rls_name=new_rls_name,
                    nzb_path=nzb_path,
                    config=config,
                    cat=cat,
                    nfo_path=nfo_path,
                    mediainfo_path=mediainfo_path,
                    attempt_suffix=" (Duplicate-Bypass)",
                )

            logger.warning(
                f"{indexer.log_name} Duplicate response, not marking posted: {rls_name}"
            )
            return SubmitResult(
                False, "duplicate", "Indexer reported duplicate; no confirmed new post"
            )

        if success:
            log_success(f"{indexer.log_name} Accepted: {rls_name}")
            resp_body = redact_text(
                response.text[:300].replace(chr(10), " ").strip(),
                secrets=(api_key, username),
            )
            logger.info(
                f"{indexer.log_name} Response: HTTP {response.status_code} | {resp_body}"
            )
            return SubmitResult(True, "success", "Indexer accepted submission")

        # Failed submission - log response for debugging
        response_secrets = (api_key, username)
        resp_trunc = redact_text(
            response.text[:500].replace("\n", " ").strip(), secrets=response_secrets
        )
        logger.warning(f"{indexer.log_name} Submission Rejected: {rls_name}")
        logger.warning(f"{indexer.log_name} Error Sample: {resp_trunc}")

        # Extra debug for "Missing parameter" errors
        if "missing parameter" in resp_trunc.lower():
            file_sizes = _estimate_payload_size(submission.file_specs)
            file_keys = [field_name for field_name, *_rest in submission.file_specs]
            logger.warning(
                f"{indexer.log_name} DEBUG: URL={redact_url(submission.url)} | "
                f"Params={redact_mapping(submission.params)} | "
                f"DataKeys={list(submission.data.keys())} | FileKeys={file_keys} | "
                f"FileSize={file_sizes} bytes"
            )

        return SubmitResult(
            False,
            "rejected",
            f"Indexer rejected submission: {resp_trunc or 'unknown rejection'}",
        )

    except requests.RequestException as e:
        # Keep the status terse so we do not leak full URLs with API keys.
        response_secrets = (api_key, username)
        if hasattr(e, "response") and e.response is not None:
            err_msg = f"HTTP {e.response.status_code} {e.response.reason or 'Error'}"
        else:
            # Connection-level errors (SSL, timeout, connection reset, etc.)
            # have no HTTP response at all. The bare exception class name
            # ("SSLError") gives no way to distinguish a cert failure from a
            # reset connection from a handshake timeout, so include the
            # exception's own message too -- redacted, since urllib3's error
            # text can embed the full request URL including the API key.
            detail = (
                redact_text(e, secrets=response_secrets)[:300]
                .replace("\n", " ")
                .strip()
            )
            err_msg = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
        if hasattr(e, "response") and e.response is not None:
            body = e.response.text[:3000]
            meta = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,300})',
                body,
                re.IGNORECASE,
            ) or re.search(
                r'<meta[^>]+content=["\']([^"\']{1,300})["\'][^>]+name=["\']description["\']',
                body,
                re.IGNORECASE,
            )
            if meta:
                resp_body = meta.group(1).strip()
            elif body.strip().startswith("<"):
                resp_body = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:200]
            else:
                resp_body = body[:200].replace("\n", " ").strip()
            err_msg += f" | Body: {redact_text(resp_body, secrets=response_secrets)}"

        logger.warning(f"{indexer.log_name} submission failed: {err_msg}")

        return SubmitResult(False, "network_error", err_msg)

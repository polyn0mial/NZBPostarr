"""
NZB parsing and file probing for NZB reposting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]

from logic.stream.nntp import StreamError, UsenetReader, _ensure_message_id, decode_yenc_article


def _guess_filename(subject: str) -> str:
    match = re.search(r'"([^"]+)"', subject)
    if match:
        return match.group(1).strip()
    subject = re.sub(r"\s+yEnc.*$", "", subject, flags=re.I).strip()
    return subject or "streamed.bin"


def _read_nzb_xml_root(source_path: Path) -> ET.Element:
    payload = source_path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]

    try:
        return ET.fromstring(payload)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise StreamError(f"Invalid NZB XML: {exc}") from exc


def read_nzb_head_metadata(source_path: Path, *, root: Optional[ET.Element] = None) -> dict[str, str]:
    if root is None:
        root = _read_nzb_xml_root(source_path)
    metadata: dict[str, str] = {}
    meta_nodes = root.findall(".//{*}head/{*}meta") + root.findall(".//head/meta")
    for meta_node in meta_nodes:
        key = str(meta_node.attrib.get("type") or "").strip().lower()
        value = str(meta_node.text or "").strip()
        if key and value and key not in metadata:
            metadata[key] = value
    return metadata


def parse_nzb_structure(source_path: Path) -> dict[str, Any]:
    """Parse the NZB XML into a compact manifest skeleton."""
    root = _read_nzb_xml_root(source_path)
    metadata = read_nzb_head_metadata(source_path, root=root)

    files: list[dict[str, Any]] = []
    for file_node in root.findall(".//{*}file") + root.findall(".//file"):
        subject = str(file_node.attrib.get("subject") or "").strip()
        poster = str(file_node.attrib.get("poster") or "").strip()
        date = str(file_node.attrib.get("date") or "").strip()
        groups = [grp.text.strip() for grp in file_node.findall(".//{*}group") if grp.text and grp.text.strip()]
        if not groups:
            groups = [grp.text.strip() for grp in file_node.findall(".//group") if grp.text and grp.text.strip()]

        segments: list[dict[str, Any]] = []
        segment_nodes = file_node.findall(".//{*}segment") or file_node.findall(".//segment")
        for segment_node in segment_nodes:
            message_id = _ensure_message_id(segment_node.text or "")
            try:
                number = int(segment_node.attrib.get("number") or 0)
            except (TypeError, ValueError):
                number = 0
            try:
                article_bytes = int(segment_node.attrib.get("bytes") or 0)
            except (TypeError, ValueError):
                article_bytes = 0
            segments.append(
                {
                    "number": number,
                    "bytes": article_bytes,
                    "message_id": message_id,
                }
            )

        segments.sort(key=lambda entry: (entry["number"], entry["message_id"]))
        if not segments:
            continue

        files.append(
            {
                "subject": subject,
                "poster": poster,
                "date": date,
                "groups": groups,
                "name": _guess_filename(subject),
                "size": 0,
                "segments": segments,
            }
        )

    if not files:
        raise StreamError("NZB does not contain any <file> entries")

    return {
        "source_path": str(source_path),
        "source_name": source_path.name,
        "release_name": source_path.stem,
        "metadata": metadata,
        "files": files,
        "total_size": 0,
    }


def probe_file_details(file_entry: dict[str, Any], reader: Optional[UsenetReader] = None) -> tuple[str, int]:
    """Determine the exact filename and raw size from the first segment's yEnc header."""
    own_reader = reader is None
    reader = reader or UsenetReader()
    try:
        lines = reader.body(file_entry["segments"][0]["message_id"])
        _, meta = decode_yenc_article(lines)
        name = str(meta.get("name") or file_entry.get("name") or _guess_filename(file_entry.get("subject", ""))).strip()
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            size = sum(int(seg.get("bytes") or 0) for seg in file_entry.get("segments", []))
        if size <= 0:
            raise StreamError(f"Unable to determine file size for {name}")
        return name, size
    finally:
        if own_reader:
            reader.close()

#!/usr/bin/env python3
"""Validate or package the public source tree."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_BASENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "config.yaml",
    "config.yml",
    "deploy_config.json",
    "readme.txt",
}
_FORBIDDEN_SUFFIXES = {
    ".db",
    ".jks",
    ".key",
    ".keystore",
    ".log",
    ".nzb",
    ".p12",
    ".par2",
    ".pem",
    ".pfx",
    ".rar",
    ".sqlite",
    ".sqlite3",
}
_FORBIDDEN_PARTS = {
    ".claude",
    ".codex",
    ".cursor",
    ".idea",
    ".local",
    ".vscode",
    "backups",
    "node_modules",
}
_CONTENT_RULES = {
    "developer Windows path": re.compile(
        rb"(?i)(?:\b[A-Z]:[\\/]NEWProjects[\\/]"
        rb"|\b[A-Z]:[\\/]Users[\\/](?!username(?:[\\/]|\b)|example(?:[\\/]|\b)|user(?:[\\/]|\b))"
        rb"|\b[A-Z]:[\\/](?:Documents|Downloads|Desktop)[\\/])"
    ),
    "developer Unix home path": re.compile(
        rb"/(?:home|Users)/(?!youruser(?:/|\b)|username(?:/|\b)|user(?:/|\b)"
        rb"|example(?:/|\b)|nzbpostarr(?:/|\b))[A-Za-z0-9._-]+/"
    ),
    "unsafe world-writable permissions": re.compile(rb"(?i)\bchmod\s+(?:-[^\s]+\s+)*777\b"),
    "root SSH target": re.compile(rb"(?i)\broot@[A-Za-z0-9._-]+"),
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPO_ROOT / raw.decode("utf-8", "surrogateescape")
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def _forbidden_path_reason(relative_path: PurePosixPath) -> str | None:
    lowered_parts = tuple(part.casefold() for part in relative_path.parts)
    basename = lowered_parts[-1]
    if basename in _FORBIDDEN_BASENAMES:
        return f"private filename {basename!r}"
    if basename.startswith(".env."):
        return "environment-specific .env file"
    if basename.startswith("deploy_config") and basename != "deploy_config.example.json":
        return "machine-specific deploy configuration"
    if any(part in _FORBIDDEN_PARTS for part in lowered_parts):
        return "runtime/developer-only directory"
    if any(basename.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES):
        return "runtime, archive, or credential-bearing file type"
    if lowered_parts and lowered_parts[0] == "data":
        return "application runtime data"
    return None


def find_violations() -> list[str]:
    """Return public-tree path and content violations."""
    violations: list[str] = []
    for path in _candidate_paths():
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(REPO_ROOT).as_posix())
        path_reason = _forbidden_path_reason(relative)
        if path_reason:
            violations.append(f"{relative}: {path_reason}")
            continue

        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        for label, pattern in _CONTENT_RULES.items():
            match = pattern.search(raw)
            if match:
                line_number = raw.count(b"\n", 0, match.start()) + 1
                violations.append(f"{relative}:{line_number}: {label}")
    return violations


def check_public_tree() -> None:
    """Raise when the prospective public tree contains private/runtime data."""
    violations = find_violations()
    if violations:
        detail = "\n".join(f"  - {violation}" for violation in violations)
        raise RuntimeError(f"Public release safety check failed:\n{detail}")
    print("Public release safety check passed.")


def _safe_version(raw_version: str) -> str:
    version = raw_version.strip()
    if not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Version must contain only letters, numbers, dots, underscores, and hyphens")
    return version


def build_release(version: str, output_dir: Path) -> tuple[Path, Path]:
    """Build a reproducible archive from the exact clean commit."""
    check_public_tree()
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Release archives require a clean committed worktree")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"nzbpostarr-{version}.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--prefix=nzbpostarr-{version}/",
            f"--output={archive_path}",
            "HEAD",
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_suffix(f"{archive_path.suffix}.sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="check the prospective public tree")
    build_parser = subparsers.add_parser("build", help="build a release archive")
    build_parser.add_argument(
        "--version",
        default=_git("describe", "--tags", "--always", "--dirty"),
        help="archive version label (default: git describe)",
    )
    build_parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    args = parser.parse_args()

    try:
        if args.command == "check":
            check_public_tree()
        else:
            archive_path, checksum_path = build_release(
                _safe_version(args.version),
                args.output_dir.resolve(),
            )
            print(f"Built {archive_path}")
            print(f"Checksum {checksum_path}")
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

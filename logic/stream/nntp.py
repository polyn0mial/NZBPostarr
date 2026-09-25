"""
NNTP article reading and yEnc decoding for NZB reposting.
"""

from __future__ import annotations

import socket
import ssl
from typing import BinaryIO, Optional, Sequence

from core.config import NNTPServer, get_config


class StreamError(RuntimeError):
    """Base stream failure."""


class ArticleUnavailableError(StreamError):
    """Raised when an article could not be retrieved from any source server."""


class NNTPProtocolError(StreamError):
    """Raised when an NNTP server returns an unexpected response."""


def _enabled_servers() -> list[NNTPServer]:
    conf = get_config()
    return [server for server in (conf.nntp_servers or []) if getattr(server, "enabled", True)]


def _ensure_message_id(message_id: str) -> str:
    text = message_id.strip()
    if not text:
        raise StreamError("NZB segment is missing a Message-ID")
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"


def _parse_yenc_kv(line: bytes) -> dict[str, str]:
    text = line.decode("latin-1", errors="replace").strip()
    parts = text.split()
    values: dict[str, str] = {}
    for token in parts[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        values[key] = value
    return values


def decode_yenc_article(lines: Sequence[bytes]) -> tuple[bytes, dict[str, str]]:
    """Decode one yEnc article body into raw bytes."""
    ybegin: Optional[dict[str, str]] = None
    ypart: dict[str, str] = {}
    payload_lines: list[bytes] = []
    in_payload = False

    for raw in lines:
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"=ybegin"):
            ybegin = _parse_yenc_kv(line)
            in_payload = True
            continue
        if line.startswith(b"=ypart"):
            ypart = _parse_yenc_kv(line)
            continue
        if line.startswith(b"=yend"):
            break
        if in_payload:
            payload_lines.append(line)

    if not ybegin:
        raise StreamError("Article is missing a yEnc header")

    encoded = b"".join(payload_lines)
    decoded = bytearray()
    escaped = False
    for value in encoded:
        if escaped:
            decoded.append((value - 106) % 256)
            escaped = False
            continue
        if value == 61:  # '='
            escaped = True
            continue
        decoded.append((value - 42) % 256)

    meta = dict(ybegin)
    meta.update({f"part_{k}": v for k, v in ypart.items()})
    return bytes(decoded), meta


class SimpleNNTPConnection:
    """Tiny NNTP client for BODY retrieval.

    Python 3.14 removed stdlib nntplib, so this keeps the feature self-contained.
    """

    def __init__(self, server: NNTPServer, timeout: float = 60.0):
        self.server = server
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.reader: Optional[BinaryIO] = None
        self.writer: Optional[BinaryIO] = None
        self._connect()

    def _connect(self) -> None:
        sock = socket.create_connection((self.server.host, int(self.server.port)), timeout=self.timeout)
        sock.settimeout(self.timeout)
        if self.server.ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.server.host)
        self.sock = sock
        self.reader = sock.makefile("rb")
        self.writer = sock.makefile("wb")

        code, message = self._read_status_line()
        if code not in {200, 201}:
            raise NNTPProtocolError(f"{self.server.name}: unexpected greeting {code} {message}")

        self._send_command("MODE READER", ok_codes={200, 201, 480, 500})
        self._send_command(f"AUTHINFO USER {self.server.user}", ok_codes={281, 381})
        self._send_command(f"AUTHINFO PASS {self.server.password}", ok_codes={281})

    def close(self) -> None:
        try:
            if self.writer is not None:
                try:
                    self.writer.write(b"QUIT\r\n")
                    self.writer.flush()
                except OSError:
                    pass
        finally:
            for stream in (self.reader, self.writer, self.sock):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            self.reader = None
            self.writer = None
            self.sock = None

    def _readline(self) -> bytes:
        if self.reader is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")
        line = self.reader.readline()
        if not line:
            raise NNTPProtocolError(f"{self.server.name}: connection closed unexpectedly")
        return line

    def _read_status_line(self) -> tuple[int, str]:
        line = self._readline().decode("latin-1", errors="replace").rstrip("\r\n")
        if len(line) < 3 or not line[:3].isdigit():
            raise NNTPProtocolError(f"{self.server.name}: invalid NNTP response {line!r}")
        return int(line[:3]), line[4:] if len(line) > 4 else ""

    def _send_command(self, command: str, ok_codes: set[int]) -> tuple[int, str]:
        if self.writer is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")
        self.writer.write(command.encode("latin-1", errors="replace") + b"\r\n")
        self.writer.flush()
        code, message = self._read_status_line()
        if code not in ok_codes:
            raise NNTPProtocolError(f"{self.server.name}: {command} -> {code} {message}")
        return code, message

    def body(self, message_id: str) -> list[bytes]:
        if self.writer is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")

        self.writer.write(f"BODY {message_id}\r\n".encode("latin-1", errors="replace"))
        self.writer.flush()
        code, message = self._read_status_line()
        if code == 430:
            raise ArticleUnavailableError(f"{self.server.name}: article not found: {message_id}")
        if code != 222:
            raise NNTPProtocolError(f"{self.server.name}: BODY -> {code} {message}")

        lines: list[bytes] = []
        while True:
            line = self._readline()
            if line in {b".\r\n", b".\n", b"."}:
                break
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line)
        return lines


class UsenetReader:
    """Article reader with provider fallback."""

    def __init__(self, servers: Optional[Sequence[NNTPServer]] = None):
        self.servers = list(servers or _enabled_servers())
        if not self.servers:
            raise StreamError("No enabled NNTP servers are configured")
        self._connections: dict[str, SimpleNNTPConnection] = {}
        self._preferred: Optional[str] = None

    def close(self) -> None:
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()

    def _get_connection(self, server: NNTPServer) -> SimpleNNTPConnection:
        conn = self._connections.get(server.name)
        if conn is None:
            conn = SimpleNNTPConnection(server)
            self._connections[server.name] = conn
        return conn

    def body(self, message_id: str) -> list[bytes]:
        ordered = list(self.servers)
        if self._preferred:
            ordered.sort(key=lambda srv: 0 if srv.name == self._preferred else 1)

        last_error: Optional[Exception] = None
        for server in ordered:
            try:
                conn = self._get_connection(server)
                lines = conn.body(message_id)
                self._preferred = server.name
                return lines
            except ArticleUnavailableError as exc:
                last_error = exc
                continue
            except (StreamError, OSError, ValueError) as exc:  # protocol, socket/TLS, bad port: try the next server
                last_error = exc
                old = self._connections.pop(server.name, None)
                if old is not None:
                    old.close()
                continue

        if last_error:
            raise ArticleUnavailableError(str(last_error)) from last_error
        raise ArticleUnavailableError(f"Article unavailable across all servers: {message_id}")

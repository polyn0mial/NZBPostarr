# ruff: noqa: F403,F405

"""NZBPostarr streaming tests."""

from tests.support import *

def test_headless_stream_command_queues_stream_job(monkeypatch, capsys) -> None:
    from logic import services, usenet_stream

    captured: dict[str, object] = {}

    class FakeService:
        def start_usenet_stream_job(self, **kwargs):
            captured.update(kwargs)
            return "stream-job-123"

    monkeypatch.setattr(services, "init_app", lambda: None)
    monkeypatch.setattr(services, "get_upload_service", lambda: FakeService())
    monkeypatch.setattr(usenet_stream, "resolve_source_nzb_paths", lambda _source: [Path("D:/tmp/example.nzb")])

    rc = cli_run.run_headless(
        [
            "stream",
            "D:/tmp/example.nzb",
            "--category",
            "tv",
            "--posting-server",
            "Primary",
            "--indexer",
            "geek",
            "--submit-mode",
            "post_only",
            "--test",
            "--skip-duplicate-check",
        ]
    )

    assert rc == 0
    assert captured == {
        "category": "tv",
        "stream_source_path": str(Path("D:/tmp/example.nzb")),
        "stream_source_name": "example.nzb",
        "release_name": None,
        "test_mode": True,
        "enable_duplicate_check": False,
        "indexer_id": "geek",
        "posting_server_name": "Primary",
        "submit_mode": "post_only",
    }
    output = capsys.readouterr().out
    assert "stream-job-123" in output
    assert "Stream Jobs Queued" in output

def test_stream_request_normalization_contract() -> None:
    from logic import usenet_stream

    options = usenet_stream.normalize_stream_request(
        upload_filename="release.nzb",
        source_path=None,
        monitor_folder=False,
        category=" anime ",
        submit_mode="post-only",
    )
    assert options.source_path == ""
    assert options.category == "anime"
    assert options.submit_mode == "post_only"

    invalid_cases = [
        {"upload_filename": "a.nzb", "source_path": "/srv/a.nzb", "monitor_folder": False},
        {"upload_filename": None, "source_path": None, "monitor_folder": False},
        {"upload_filename": "a.nzb", "source_path": None, "monitor_folder": True},
        {"upload_filename": "a.txt", "source_path": None, "monitor_folder": False},
    ]
    for values in invalid_cases:
        with pytest.raises(usenet_stream.StreamError):
            usenet_stream.normalize_stream_request(
                **values,
                category=None,
                submit_mode=None,
            )

def test_headless_stream_command_can_save_monitor_definition(monkeypatch, capsys) -> None:
    from logic import services, usenet_stream

    captured: dict[str, object] = {}

    monkeypatch.setattr(services, "init_app", lambda: None)

    def fake_add_stream_monitor(**kwargs):
        captured.update(kwargs)
        return {
            "id": "mon-001",
            "folder_path": "D:/watch",
            "category": kwargs["category"],
            "posting_server_name": kwargs["posting_server_name"],
            "submit_mode": kwargs["submit_mode"],
            "indexer_id": kwargs["indexer_id"],
            "enable_duplicate_check": kwargs["enable_duplicate_check"],
            "test_mode": kwargs["test_mode"],
        }

    monkeypatch.setattr(usenet_stream, "add_stream_monitor", fake_add_stream_monitor)

    rc = cli_run.run_headless(
        [
            "stream",
            "D:/watch",
            "--monitor",
            "--category",
            "movies",
            "--posting-server",
            "Primary",
            "--indexer",
            "planet",
        ]
    )

    assert rc == 0
    assert captured == {
        "folder_path": "D:/watch",
        "category": "movies",
        "posting_server_name": "Primary",
        "submit_mode": "post_and_submit",
        "indexer_id": "planet",
        "enable_duplicate_check": True,
        "test_mode": False,
    }
    output = capsys.readouterr().out
    assert "Stream Monitor Saved" in output
    assert "active folder watching only runs in the long-lived app/WebUI process" in output

def test_headless_stream_monitors_list_json(monkeypatch, capsys) -> None:
    from logic import services, usenet_stream

    monkeypatch.setattr(services, "init_app", lambda: None)
    monkeypatch.setattr(
        usenet_stream,
        "list_stream_monitors",
        lambda: [
            {
                "id": "mon-001",
                "folder_path": "D:/watch",
                "category": "tv",
                "posting_server_name": "Primary",
                "submit_mode": "post_only",
                "indexer_id": "geek",
                "enable_duplicate_check": True,
                "test_mode": False,
                "last_job": {"job_id": "job-1", "status": "completed"},
            }
        ],
    )

    rc = cli_run.run_headless(["stream-monitors", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "mon-001"
    assert payload[0]["last_job"]["status"] == "completed"

def test_untrusted_nzb_xml_rejects_entity_expansion(tmp_path) -> None:
    from logic import usenet_stream

    source = tmp_path / "unsafe.nzb"
    source.write_text(
        '<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY boom "expanded">]><nzb>&boom;</nzb>',
        encoding="utf-8",
    )

    with pytest.raises(usenet_stream.StreamError, match="Invalid NZB XML"):
        usenet_stream._read_nzb_xml_root(source)

def test_parse_nyuu_article_bytes_decimal_units():
    from logic.pipeline.posting import _parse_nyuu_article_bytes

    # Nyuu config commonly uses "1M" style values; we parse in decimal.
    assert _parse_nyuu_article_bytes("1M") == 1_000_000
    assert _parse_nyuu_article_bytes("700K") == 700_000

    # Explicit binary units are still supported.
    assert _parse_nyuu_article_bytes("1 MiB") == 1024**2

def test_upload_stream_manifest_does_not_override_nzb_subject(tmp_path, monkeypatch) -> None:
    from logic import usenet_stream

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"files": [{"name": "sample.bin", "size": 10}]}', encoding="utf-8")

    nzb_path = tmp_path / "streamed.nzb"
    captured: dict[str, list[str]] = {}

    class DummyConf:
        def get_nzb_path(self, name: str) -> Path:
            return tmp_path / f"{name}.nzb"

    conf = DummyConf()
    conf.alt_bins = ["alt.binaries.misc"]
    conf.article_size = "1M"
    conf.nyuu_path = "nyuu"
    conf.poster = "Anonymous"
    conf.script_dir = tmp_path

    server = SimpleNamespace(
        name="Primary",
        host="news.example.com",
        port=563,
        user="user",
        password="pass",
        ssl=True,
        max_connections=10,
    )

    def fake_run_command(cmd, *_args, **_kwargs):
        captured["cmd"] = cmd
        _write_valid_test_nzb(nzb_path)
        return True, ["Finished uploading in 00:00:01"]

    monkeypatch.setattr(usenet_stream, "get_config", lambda: conf)
    monkeypatch.setattr(usenet_stream, "build_procjson_inputs", lambda *_args, **_kwargs: ["procjson://input"])
    monkeypatch.setattr(usenet_stream, "run_command", fake_run_command)

    result, generated_nzb = usenet_stream.upload_stream_manifest(
        manifest_path,
        "Release.Name",
        server,
        10,
        nzb_path=nzb_path,
    )

    assert result is not None
    assert generated_nzb == nzb_path
    assert "--nzb-subject" not in captured["cmd"]

def test_submit_to_indexer_streams_nzb_payload(tmp_path, monkeypatch) -> None:
    seen = _capture_submit_request(monkeypatch)

    def fake_request(*_args, **kwargs):
        files = kwargs["files"]
        nzb_handle = files["nzb"][1]
        seen["files"] = files
        seen["nzb_handle"] = nzb_handle
        assert hasattr(nzb_handle, "read")
        assert not isinstance(nzb_handle, (bytes, bytearray))
        assert nzb_handle.closed is False
        return type(
            "DummyResponse",
            (),
            {"status_code": 200, "text": "OK", "raise_for_status": lambda self: None, "json": lambda self: {}},
        )()

    monkeypatch.setattr(registry_mod.requests, "request", fake_request)

    nzb_file = _make_sample_nzb(tmp_path)

    indexer = IndexerDefinition(
        id="geek",
        name="NZBGeek",
        submit_url="https://example.invalid/api",
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
        success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
    )

    ok, status, reason = submit_to_indexer(
        indexer=indexer,
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=nzb_file,
        config=_DummySubmitConfig(),
    )

    assert ok is True
    assert status == "success"
    assert "accepted" in reason.lower()
    assert seen["nzb_handle"].closed is True

def test_submit_to_indexer_rebuilds_streams_for_cloudflare_retry(tmp_path, monkeypatch) -> None:
    handles = []
    waits = []

    class DummyResponse:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text
            self.is_redirect = False

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {}

    responses = [
        DummyResponse(403, "<title>Just a moment...</title>"),
        DummyResponse(200, "OK"),
    ]

    def fake_request(*_args, **kwargs):
        handle = kwargs["files"]["nzb"][1]
        if handles:
            assert handles[-1].closed is True
        assert handle.closed is False
        handles.append(handle)
        return responses.pop(0)

    monkeypatch.setattr(registry_mod.requests, "request", fake_request)
    monkeypatch.setattr(registry_mod.time, "sleep", waits.append)

    indexer = IndexerDefinition(
        id="cloudflare-retry",
        name="Cloudflare Retry",
        submit_url="https://example.invalid/api",
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
        success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
    )
    result = submit_to_indexer(
        indexer=indexer,
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=_make_sample_nzb(tmp_path),
        config=_DummySubmitConfig(),
    )

    assert result[:2] == (True, "success")
    assert waits == [15]
    assert len(handles) == 2
    assert all(handle.closed for handle in handles)

def test_decode_yenc_article_round_trips_payload() -> None:
    from logic.usenet_stream import decode_yenc_article

    def encode_yenc(payload: bytes) -> bytes:
        encoded = bytearray()
        for value in payload:
            transformed = (value + 42) % 256
            if transformed in {0, 9, 10, 13, 32, 46, 61}:
                encoded.append(61)
                encoded.append((transformed + 64) % 256)
            else:
                encoded.append(transformed)
        return bytes(encoded)

    original = b"hello usenet"
    lines = [
        b"=ybegin part=1 line=128 size=12 name=test.bin\r\n",
        encode_yenc(original) + b"\r\n",
        b"=yend size=12 part=1\r\n",
    ]

    decoded, meta = decode_yenc_article(lines)

    assert decoded == original
    assert meta["name"] == "test.bin"
    assert meta["size"] == "12"

def test_prepare_stream_manifest_uses_probe_results(tmp_path, monkeypatch) -> None:
    from logic import usenet_stream

    source = tmp_path / "release.nzb"
    source.write_text(
        """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<nzb xmlns=\"http://www.newzbin.com/DTD/2003/nzb\">
  <file poster=\"poster@example.com\" date=\"123\" subject=\"One yEnc\">
    <segments><segment bytes=\"10\" number=\"1\">one@example</segment></segments>
  </file>
  <file poster=\"poster@example.com\" date=\"124\" subject=\"Two yEnc\">
    <segments><segment bytes=\"20\" number=\"1\">two@example</segment></segments>
  </file>
</nzb>
""",
        encoding="utf-8",
    )

    class DummyReader:
        def close(self) -> None:
            return None

    monkeypatch.setattr(usenet_stream, "UsenetReader", lambda: DummyReader())

    seen: list[str] = []

    def fake_probe(file_entry, reader=None):
        seen.append(file_entry["subject"])
        if file_entry["subject"] == "One yEnc":
            return "one.bin", 111
        return "two.bin", 222

    monkeypatch.setattr(usenet_stream, "probe_file_details", fake_probe)

    manifest = usenet_stream.prepare_stream_manifest(source, release_name="Custom.Release")

    assert seen == ["One yEnc", "Two yEnc"]
    assert manifest["release_name"] == "Custom.Release"
    assert manifest["total_size"] == 333
    assert [entry["name"] for entry in manifest["files"]] == ["one.bin", "two.bin"]
    assert [entry["size"] for entry in manifest["files"]] == [111, 222]

def test_stream_nzb_upload_respects_post_only_and_selected_server(tmp_path, monkeypatch) -> None:
    from logic import usenet_stream

    source = tmp_path / "input.nzb"
    source.write_text("<nzb></nzb>", encoding="utf-8")

    primary = SimpleNamespace(
        name="Primary", host="news-a", port=119, user="u", password="p", ssl=False, max_connections=10
    )
    backup = SimpleNamespace(
        name="Backup", host="news-b", port=119, user="u", password="p", ssl=False, max_connections=10
    )
    uploaded: dict[str, str] = {}
    submit_called = {"value": False}

    monkeypatch.setattr(usenet_stream, "_enabled_servers", lambda: [primary, backup])
    monkeypatch.setattr(
        usenet_stream,
        "prepare_stream_manifest",
        lambda _source, _release: {"files": [{"name": "one.bin", "size": 10}], "total_size": 10},
    )
    monkeypatch.setattr(usenet_stream, "write_stream_manifest", lambda manifest, destination: destination)

    def fake_upload(manifest_path, release_name, server, total_size, *, nzb_path=None):
        uploaded["server_name"] = server.name
        return ({"duration": 1.0, "speed_bps": 2.0, "server_name": server.name}, tmp_path / "out.nzb")

    monkeypatch.setattr(usenet_stream, "upload_stream_manifest", fake_upload)
    monkeypatch.setattr(usenet_stream, "record_nntp_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(usenet_stream, "update_db_destination", lambda *args, **kwargs: True)
    monkeypatch.setattr(usenet_stream, "submit_api", lambda *args, **kwargs: submit_called.__setitem__("value", True))

    result = usenet_stream.stream_nzb_upload(
        source_path=source,
        category="misc",
        release_name="Custom.Release",
        posting_server_name="Backup",
        submit_mode="post_only",
        manifest_path=tmp_path / "manifest.json",
    )

    assert uploaded["server_name"] == "Backup"
    assert result["posting_server_name"] == "Backup"
    assert result["submit_mode"] == "post_only"
    assert submit_called["value"] is False

def test_add_stream_monitor_persists_configuration(tmp_path, monkeypatch) -> None:
    from logic import usenet_stream

    watch_folder = tmp_path / "watch"
    watch_folder.mkdir()
    server = SimpleNamespace(name="Primary")

    monkeypatch.setattr(usenet_stream, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))
    monkeypatch.setattr(usenet_stream, "_enabled_servers", lambda: [server])

    usenet_stream._stream_monitor_entries.clear()
    usenet_stream._stream_monitor_pending.clear()
    usenet_stream._stream_monitor_known.clear()
    usenet_stream._stream_monitor_state_loaded = False

    monitor = usenet_stream.add_stream_monitor(folder_path=str(watch_folder), category="misc")
    listed = usenet_stream.list_stream_monitors()

    assert monitor["folder_path"] == str(watch_folder.resolve())
    assert len(listed) == 1
    assert listed[0]["posting_server_name"] == "Primary"
    assert usenet_stream.remove_stream_monitor(monitor["id"]) is True

def test_stream_monitor_tracks_last_job_snapshot(tmp_path, monkeypatch) -> None:
    from logic import usenet_stream

    watch_folder = tmp_path / "watch"
    watch_folder.mkdir()
    server = SimpleNamespace(name="Primary")

    monkeypatch.setattr(usenet_stream, "get_config", lambda: SimpleNamespace(script_dir=tmp_path))
    monkeypatch.setattr(usenet_stream, "_enabled_servers", lambda: [server])

    usenet_stream._stream_monitor_entries.clear()
    usenet_stream._stream_monitor_pending.clear()
    usenet_stream._stream_monitor_known.clear()
    usenet_stream._stream_monitor_state_loaded = False

    monitor = usenet_stream.add_stream_monitor(folder_path=str(watch_folder), category="misc")
    usenet_stream.record_stream_monitor_job(
        monitor["id"],
        {
            "job_id": "abcd1234",
            "status": "completed",
            "progress": "Finished in 30s",
            "display_name": "NZB Stream - Example",
            "items_processed": 1,
            "items_total": 1,
            "item_percent": 100,
        },
    )

    listed = usenet_stream.list_stream_monitors()

    assert listed[0]["last_job"]["job_id"] == "abcd1234"
    assert listed[0]["last_job"]["status"] == "completed"
    assert listed[0]["last_job"]["display_name"] == "NZB Stream - Example"

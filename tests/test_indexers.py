# ruff: noqa: F403,F405

"""NZBPostarr indexers tests."""

from tests.support import *

def test_indexer_secret_resolution() -> None:
    idx = IndexerDefinition(
        id="geek",
        name="NZBGeek",
        submit_url="https://example.invalid/submit",
        enabled=True,
        backfill=True,
    )

    for case_name, config_overrides, expected in (
        ("mapping-lookup", {"api_keys": {"geek": "abc123"}}, "abc123"),
        ("field-fallback", {"geek_api_key": "field_key"}, "field_key"),
    ):
        conf = Config.model_construct(None, **config_overrides)
        assert resolve_indexer_api_key(idx, conf) == expected, case_name

    username_idx = IndexerDefinition(
        id="omg",
        name="OMGwtfnzbs",
        submit_url="https://example.invalid/submit",
        method="CURL",
        curl_template="https://example.invalid/api?user={username}&api={api_key}",
        enabled=True,
        backfill=True,
    )
    conf = Config.model_construct(usernames={"omg": "myuser"})
    assert resolve_indexer_username(username_idx, conf) == "myuser"

def test_ordinary_api_secret_masking_and_save_merge() -> None:
    from core.redaction import SECRET_MASK, redact_mapping, redact_text, redact_url

    current_server = SimpleNamespace(
        name="Primary",
        user="poster",
        password="private-password",
    )
    conf = SimpleNamespace(
        api_keys={"geek": "private-key"},
        usernames={"omg": "private-user"},
        nntp_servers=[current_server],
        web_password="private-web-password",
    )
    masked = app_mod._mask_config_secrets(
        {
            "enable_password": True,
            "api_keys": conf.api_keys,
            "nntp_servers": [
                {"name": "Primary", "user": current_server.user, "password": current_server.password}
            ],
        }
    )

    assert masked["enable_password"] is True
    assert masked["api_keys"]["geek"] == SECRET_MASK
    assert masked["nntp_servers"][0]["user"] == SECRET_MASK
    assert masked["nntp_servers"][0]["password"] == SECRET_MASK

    merged = app_mod._merge_masked_secret_updates(
        {
            "api_keys": {"geek": SECRET_MASK},
            "usernames": {"omg": SECRET_MASK},
            "web_password": SECRET_MASK,
            "nntp_servers": [
                {
                    "name": "Primary",
                    "host": "news.example.test",
                    "user": SECRET_MASK,
                    "password": SECRET_MASK,
                }
            ],
        },
        conf,
    )

    assert merged["api_keys"]["geek"] == "private-key"
    assert merged["usernames"]["omg"] == "private-user"
    assert merged["web_password"] == "private-web-password"
    assert merged["nntp_servers"][0]["user"] == "poster"
    assert merged["nntp_servers"][0]["pass"] == "private-password"
    assert "password" not in merged["nntp_servers"][0]

    safe_url = redact_url("https://poster:secret@example.test/api?apikey=abc&cat=movies")
    safe_curl_url = redact_url(
        "https://example.test/api-upload.php?inf=err1&user=poster&api=standalone-api-secret"
    )
    safe_text = redact_text(
        "request https://example.test/api?token=abc failed for private-key",
        secrets=("private-key",),
    )
    safe_mapping = redact_mapping({"apikey": "abc", "cat": "movies"})
    for safe_value in (safe_url, safe_curl_url, safe_text, str(safe_mapping)):
        assert "abc" not in safe_value
    assert "secret" not in safe_url
    assert "standalone-api-secret" not in safe_curl_url
    assert "api=%5BREDACTED%5D" in safe_curl_url
    assert "private-key" not in safe_text

    # Credentials ride on non-http schemes too (NNTP is this app's whole point).
    for raw, leaked in (
        ("connect failed: nntps://bob:hunter2@news.example.test:563/", "hunter2"),
        ("ftp://admin:p4ssw0rd@files.example.test/dump", "p4ssw0rd"),
    ):
        masked = redact_text(raw)
        assert leaked not in masked, raw
        assert "news.example.test" in masked or "files.example.test" in masked

def test_indexer_ui_metadata_never_returns_configured_credentials() -> None:
    idx = IndexerDefinition(
        id="geek",
        name="NZBGeek",
        submit_url="https://example.invalid/submit",
        enabled=True,
        backfill=True,
    )
    conf = _DummySubmitConfig(api_key="private-key", username="private-user")

    payload = idx.to_ui_dict(conf)

    assert payload["has_api_key"] is True
    assert payload["api_key"] == ""
    assert payload["username"] == ""

def test_estimate_nyuu_post_percent_uses_read_as_lower_bound():
    from logic.uploaders import _estimate_nyuu_post_percent

    # If our estimate is too small, the read counter should prevent us from
    # hitting 99% early for a long time.
    assert _estimate_nyuu_post_percent(100, 10_000, 9_000) == 97

    # Still cap at 99% until we have an explicit completion signal.
    assert _estimate_nyuu_post_percent(10_000, 10_000, 10_000) == 99

def test_upload_item_does_not_override_nzb_subject(tmp_path, monkeypatch) -> None:
    from logic import uploaders

    tmp_sub = tmp_path / "tmp"
    item_dir = tmp_sub / "Release.Name"
    item_dir.mkdir(parents=True)
    (item_dir / "sample.bin").write_bytes(b"0123456789")

    nzb_path = tmp_path / "Release.Name.nzb"
    captured: dict[str, list[str]] = {}

    class DummyConf:
        def get_nzb_path(self, name: str) -> Path:
            return tmp_path / f"{name}.nzb"

    conf = DummyConf()
    conf.tmp_sub = tmp_sub
    conf.include_readme = False
    conf.test_run = False
    conf.verbose = False
    conf.article_size = "1M"
    conf.nyuu_path = "nyuu"
    conf.poster = "Anonymous"
    conf.alt_bins = ["alt.binaries.misc"]
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

    monkeypatch.setattr(uploaders, "get_config", lambda: conf)
    monkeypatch.setattr(uploaders, "run_command", fake_run_command)
    result = uploaders.upload_item("Release.Name", server, nzb_path=nzb_path)

    assert result is not None
    assert "--nzb-subject" not in captured["cmd"]

def test_submit_to_indexer_flags_missing_api_key_as_misconfigured() -> None:
    indexer = IndexerDefinition(
        id="geek",
        name="NZBGeek",
        submit_url="https://example.invalid/api",
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
    )

    ok, status, reason = submit_to_indexer(
        indexer=indexer,
        rls_name="Some.Release.2026.1080p.WEB-DL",
        nzb_path=Path("unused.nzb"),
        config=_DummySubmitConfig(api_key=""),
    )

    assert ok is False
    assert status == "misconfigured"
    assert "api key" in reason.lower()

def test_submit_to_indexer_rejects_unknown_category_without_default_fallback(tmp_path) -> None:
    nzb_file = _make_sample_nzb(tmp_path)

    indexer = IndexerDefinition(
        id="movies-only",
        name="Movies Only",
        submit_url="https://example.invalid/api",
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
        categories=registry_mod.CategoryMapping(tv="", movie="2040", misc="5000", default="5000"),
        success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
    )

    ok, status, reason = submit_to_indexer(
        indexer=indexer,
        rls_name="Show.Name.S01.1080p.WEB-DL",
        nzb_path=nzb_file,
        config=_DummySubmitConfig(),
        cat="tv",
    )

    assert ok is False
    assert status == "misconfigured"
    assert "category" in reason.lower()


def test_audiobook_category_mapping_prefers_direct_code_then_books_fallback() -> None:
    from core.utils import normalize_submission_category

    direct = registry_mod.CategoryMapping(audiobooks="3030", books="7020")
    fallback = registry_mod.CategoryMapping(books="7020")
    missing = registry_mod.CategoryMapping(movie="2040")

    assert normalize_submission_category("audiobook") == "audiobooks"
    assert normalize_submission_category("audiobooks") == "audiobooks"
    assert direct.resolve_code("audiobooks") == ("3030", "audiobooks", True)
    assert fallback.resolve_code("audiobooks") == ("7020", "books", False)
    assert missing.resolve_code("audiobooks") == (None, None, False)


def test_available_categories_exposes_dedicated_audiobook_metadata(monkeypatch) -> None:
    indexer = IndexerDefinition(
        id="audio-check",
        name="Audio Check",
        submit_url="https://example.invalid/api",
        categories=registry_mod.CategoryMapping(audiobooks="3030"),
    )
    monkeypatch.setattr(
        registry_mod,
        "get_registry",
        lambda: SimpleNamespace(all=lambda: [indexer]),
    )

    assert registry_mod.get_available_categories() == [
        {
            "id": "audiobooks",
            "key": "audiobooks",
            "label": "Audiobooks",
            "icon": "headphones",
            "color": "teal-400",
            "indexers": [
                {
                    "id": "audio-check",
                    "name": "Audio Check",
                    "favicon_url": None,
                    "color": "#808080",
                }
            ],
        }
    ]


def test_books_mapping_advertises_only_books_for_all_jobs(monkeypatch) -> None:
    from logic import processing

    indexer = IndexerDefinition(
        id="books-check",
        name="Books Check",
        submit_url="https://example.invalid/api",
        categories=registry_mod.CategoryMapping(books="7020"),
    )
    monkeypatch.setattr(
        registry_mod,
        "get_registry",
        lambda: SimpleNamespace(all=lambda: [indexer]),
    )

    categories = registry_mod.get_available_categories()
    # D03: a books-only mapping no longer mirrors an Audiobooks category.
    assert [category["id"] for category in categories] == ["books"]
    assert processing._resolve_job_categories("all") == ["books"]
    assert processing._resolve_job_categories("mixed") == ["books"]


def test_submit_to_indexer_uses_expected_category_mapping(tmp_path, monkeypatch) -> None:
    cases = [
        (
            "tv-pack-falls-back-to-tv",
            IndexerDefinition(
                id="geek",
                name="NZBGeek",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(tv="5040", movie="2040", misc="5000", default="5000"),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Show.Name.S01.1080p.WEB-DL",
            "tv_pack",
            "params",
            "cat",
            "5040",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "tv-uses-tv-mapping",
            IndexerDefinition(
                id="tv-check",
                name="TV Check",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(
                    tv="5040", movie="2040", anime="5070", misc="5000", default="5000"
                ),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Show.Name.S00E01.1080p.WEB-DL",
            "tv",
            "params",
            "cat",
            "5040",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "anime-uses-anime-mapping",
            IndexerDefinition(
                id="anime-check",
                name="Anime Check",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(
                    tv="5040", movie="2040", anime="5070", misc="5000", default="5000"
                ),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Anime.Name.S01E01.1080p.WEB-DL",
            "anime",
            "params",
            "cat",
            "5070",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "geek-dvd-remux-uses-movie-sd",
            IndexerDefinition(
                id="geek",
                name="NZBGeek",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(
                    movie="2040", movie_sd="2030", movie_hd="2040", misc="5000", default="5000"
                ),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Movie.Name.1999.DVD.REMUX.NTSC",
            "movies",
            "params",
            "cat",
            "2030",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "omg-dvd-uses-movie-dvd",
            IndexerDefinition(
                id="omg",
                name="OMGwtfnzbs",
                submit_url="https://example.invalid/api-upload.php",
                method="CURL",
                curl_template="https://example.invalid/api-upload.php?user={username}&api={api_key}",
                auth=AuthConfig(method="curl_url"),
                category_param="catid",
                categories=registry_mod.CategoryMapping(
                    movie="movie",
                    movie_sd="15",
                    movie_hd="16",
                    movie_dvd="17",
                    misc="29",
                    default="video",
                ),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Movie.Name.2001.DVD.REMUX.PAL",
            "movies",
            "data",
            "catid",
            "17",
            "OK",
            None,
            _DummySubmitConfig(username="omg-user"),
        ),
        (
            "shipped-omg-anime-uses-tv-category",
            IndexerDefinition(**yaml.safe_load(_read_repo_text("indexers", "omg.yaml"))),
            "Pokemon.S20E99.Orla.Grounded.1080p.WEBRip.x265",
            "anime",
            "data",
            "catid",
            "tv",
            "OK",
            None,
            _DummySubmitConfig(username="omg-user"),
        ),
        (
            "audiobook-uses-dedicated-mapping",
            IndexerDefinition(
                id="audio-check",
                name="Audio Check",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(
                    audiobooks="3030",
                    books="7020",
                    misc="5000",
                ),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Author.Name.Novel.Unabridged.MP3",
            "audiobooks",
            "params",
            "cat",
            "3030",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "audiobook-falls-back-to-books-mapping",
            IndexerDefinition(
                id="books-check",
                name="Books Check",
                submit_url="https://example.invalid/api",
                auth=AuthConfig(method="query_param", api_key_param="apikey"),
                categories=registry_mod.CategoryMapping(books="7020", misc="5000"),
                success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
            ),
            "Author.Name.Novel.Unabridged.MP3",
            "audiobooks",
            "params",
            "cat",
            "7020",
            "OK",
            None,
            _DummySubmitConfig(),
        ),
        (
            "shipped-indexer-books-category",
            IndexerDefinition(**yaml.safe_load(_read_repo_text("indexers", "in.yaml"))),
            "Author.Name.Book.Title.2026.RETAIL.EPUB",
            "books",
            "data",
            "category",
            "books",
            '{"success":true}',
            {"success": True},
            _DummySubmitConfig(),
        ),
    ]

    for (
        case_name,
        indexer,
        release_name,
        category,
        capture_key,
        capture_field,
        expected_value,
        response_text,
        response_json,
        config,
    ) in cases:
        seen = _capture_submit_request(monkeypatch, text=response_text, json_payload=response_json)
        nzb_file = _make_sample_nzb(tmp_path / case_name)

        ok, status, _reason = submit_to_indexer(
            indexer=indexer,
            rls_name=release_name,
            nzb_path=nzb_file,
            config=config,
            cat=category,
        )

        assert ok is True, case_name
        assert status == "success", case_name
        assert seen[capture_key][capture_field] == expected_value, case_name

def test_build_pending_summary_prefers_backfill_indexers_for_task_totals() -> None:
    summary = pending_view.build_pending_summary(
        {
            "tv": [{"episode_count": 1, "indexers": {"geek": True, "planet": False}}],
            "movies": [{"name": "Movie.1", "indexers": {"geek": False, "planet": True}}],
            "misc": [],
            "external": [],
        },
        [
            {"id": "geek", "backfill": False},
            {"id": "planet", "backfill": True},
        ],
    )

    assert summary["item_total"] == 1
    assert summary["red_indexers"] == 0
    assert summary["task_total"] == 0
    assert summary["total"] == 0

def test_pending_summary_includes_indexer_status_metadata(monkeypatch) -> None:
    snapshot = _make_pending_snapshot(
        indexers=[{"id": "idx1", "name": "Indexer 1"}],
        summary={
            "tv_shows": 0,
            "tv_episodes": 0,
            "movies": 1,
            "misc": 0,
            "external": 0,
            "item_total": 1,
            "task_total": 0,
            "red_indexers": 0,
            "total": 0,
        },
        cached_at=456.0,
        indexer_status_available=False,
        db_error="db down",
    )

    monkeypatch.setattr(
        app_mod,
        "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 456.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )

    result = app_mod.get_pending_summary()

    assert result["ready"] is True
    assert result["indexer_status_available"] is False
    assert result["db_error"] == "db down"

def test_force_upload_items_collapses_overlapping_tv_paths(tmp_path):
    show_dir = tmp_path / "Show.Name"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    episode = season_dir / "Show.Name.S01E01.mkv"
    episode.write_bytes(b"x")

    captured: dict[str, object] = {}

    class DummyService:
        def start_processing_job_requests(self, requests, **kwargs):
            captured["requests"] = requests
            captured["kwargs"] = kwargs
            return ["job-1"]

    req = app_mod.ForceUploadRequest(
        items=[
            {"path": str(show_dir), "category": "tv", "itype": "TV Show"},
            {"path": str(episode), "category": "tv", "itype": "TV Episode"},
        ],
        enable_duplicate_check=True,
    )

    result = _run_async(app_mod.force_upload_items(req, service=DummyService()))

    assert result == {
        "status": "started",
        "job_ids": ["job-1"],
        "items_count": 1,
        "bulk_excluded": 0,
    }
    requests = captured["requests"]
    assert len(requests) == 1
    assert requests[0].category == "tv"
    assert requests[0].paths == (str(episode),)
    assert captured["kwargs"] == {"source": "pending-force-upload", "reuse_running": False}

def test_force_upload_items_preserves_tv_pack_directory_with_episode_children(tmp_path):
    season_dir = tmp_path / "Show.Name.S01.1080p.WEB-DL"
    season_dir.mkdir(parents=True)
    episode = season_dir / "Show.Name.S01E01.1080p.WEB-DL.mkv"
    episode.write_bytes(b"x")

    captured: dict[str, object] = {}

    class DummyService:
        def start_processing_job_requests(self, requests, **kwargs):
            captured["requests"] = requests
            captured["kwargs"] = kwargs
            return ["job-1"]

    req = app_mod.ForceUploadRequest(
        items=[
            {"path": str(season_dir), "category": "tv", "itype": "TV Show"},
            {"path": str(episode), "category": "tv", "itype": "TV Episode"},
        ],
        enable_duplicate_check=True,
    )

    result = _run_async(app_mod.force_upload_items(req, service=DummyService()))

    assert result == {
        "status": "started",
        "job_ids": ["job-1"],
        "items_count": 2,
        "bulk_excluded": 0,
    }
    requests = captured["requests"]
    assert len(requests) == 1
    assert requests[0].category == "tv"
    assert requests[0].paths == (str(season_dir), str(episode))
    assert captured["kwargs"] == {"source": "pending-force-upload", "reuse_running": False}

def test_force_upload_items_keeps_mixed_categories_in_one_request(tmp_path):
    movie = tmp_path / "Movie.Name.2026.mkv"
    episode = tmp_path / "Show.Name.S01E01.mkv"
    movie.write_bytes(b"x")
    episode.write_bytes(b"x")

    captured: dict[str, object] = {}

    class DummyService:
        def start_processing_job_requests(self, requests, **kwargs):
            captured["requests"] = requests
            captured["kwargs"] = kwargs
            return ["job-1"]

    req = app_mod.ForceUploadRequest(
        items=[
            {"path": str(movie), "category": "movies", "itype": "Movie"},
            {"path": str(episode), "category": "tv", "itype": "TV Episode"},
        ],
        enable_duplicate_check=False,
    )

    result = _run_async(app_mod.force_upload_items(req, service=DummyService()))

    assert result == {
        "status": "started",
        "job_ids": ["job-1"],
        "items_count": 2,
        "bulk_excluded": 0,
    }
    requests = captured["requests"]
    assert len(requests) == 1
    assert requests[0].category == "mixed"
    assert requests[0].paths == (str(movie), str(episode))
    assert tuple(item["category"] for item in requests[0].item_hints) == ("movies", "tv")
    assert captured["kwargs"] == {"source": "pending-force-upload", "reuse_running": False}

def test_submit_api_batch_isolates_indexer_exceptions(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    nzb_file = tmp_path / "sample.nzb"
    nzb_file.write_bytes(b"x")

    def fake_submit_api(_name, dest_id, _conf, **_kwargs):
        if dest_id == "bad":
            raise RuntimeError("boom")
        return SubmitResult(True, "success", "ok")

    monkeypatch.setattr(processing, "submit_api", fake_submit_api)

    results = processing._submit_api_batch(
        {"dests": ["bad", "good"]},
        conf=SimpleNamespace(),
        name="Show.Name.S01E01",
        priority_label="",
        unique_nzb=nzb_file,
        submission_category="tv",
        nfo_path=None,
        mediainfo_path=None,
    )

    assert results == [
        ("bad", False, "Unhandled submission exception for indexer 'bad': boom", "error"),
        ("good", True, "ok", "success"),
    ]

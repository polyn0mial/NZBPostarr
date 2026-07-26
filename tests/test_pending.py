# ruff: noqa: F403,F405

"""NZBPostarr pending tests."""

from tests.support import *

def test_start_upload_job_rejects_empty_explicit_path_request(tmp_path) -> None:
    service = _make_queue_service_stub(tmp_path)

    with pytest.raises(ValueError, match="No valid items remained after queue filtering"):
        service.start_upload_job(
            category="movies",
            paths=[],
            item_hints=[],
            reuse_running=False,
        )

def test_queue_start_filters_misc_and_missing_category_but_runs_valid_items(tmp_path, monkeypatch) -> None:
    from logic import queueing

    movie = tmp_path / "Movie.Title.(1993).mkv"
    special = tmp_path / "Show.Name.S00E01.Special.mkv"
    misc = tmp_path / "Unknown.Release.mkv"
    movie.write_bytes(b"x")
    special.write_bytes(b"x")
    misc.write_bytes(b"x")

    service = _make_queue_service_stub(
        tmp_path,
        queue_items=[
            {"id": 1, "path": str(movie), "category": "misc", "itype": "Misc", "name": movie.name},
            {"id": 2, "path": str(special), "category": "", "itype": "", "name": special.name},
            {"id": 3, "path": str(misc), "category": "misc", "itype": "Misc", "name": misc.name},
        ],
    )

    captured: dict[str, object] = {}

    def fake_start_processing_job_request(request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return "job-fixed-queue"

    service.start_processing_job_request = fake_start_processing_job_request
    monkeypatch.setattr(queueing.database, "db_remove_queue_items", lambda item_ids: len(item_ids))
    service.get_queue_items = lambda: list(service._queue_items)

    result = service.start_queue_with_details(source="queue-start", enable_duplicate_check=True, test_mode=False)

    assert result == {
        "job_ids": ["job-fixed-queue"],
        "jobs_created": 1,
        "started_items": 2,
        "skipped_items": 1,
        "remaining_staged": 1,
    }
    request = captured["request"]
    assert isinstance(request, queueing.ProcessingJobRequest)
    assert request.category == "mixed"
    assert request.paths == (str(movie), str(special))
    assert tuple(item["category"] for item in request.item_hints) == ("movies", "tv")
    assert service._queue_items == [
        {"id": 3, "path": str(misc), "category": "misc", "itype": "Misc", "name": misc.name}
    ]

def test_folder_category_hint_does_not_match_keyword_inside_ancestor_name() -> None:
    from logic.pending_scan import infer_folder_category_hint

    assert infer_folder_category_hint(Path("C:/Users/example/AppData/Local")) == ""
    assert infer_folder_category_hint(Path("C:/media/apps/releases")) == "apps"

def test_run_job_passes_queue_category_to_process_single(tmp_path, monkeypatch) -> None:
    """
    Regression test:
    `process_single()` uses `itype` as a human-friendly DB label (e.g. "TV Episode"),
    but indexer submissions must use stable category keys (tv/movies/misc/...).

    This test asserts `run_job()` passes the *queue category* through to `process_single()`
    separately from `itype`.
    """

    import logic.processing as processing

    tv_dir = tmp_path / "tv"
    tv_dir.mkdir()
    (tv_dir / "Show.S01E01.1080p.WEB-DL.mkv").write_bytes(b"x" * 10)

    _configure_run_job_basics(monkeypatch, processing, tv_dir, category="tv")

    captured: dict[str, object] = {}

    def fake_process_single(
        path: Path,
        force: bool = False,
        itype: str = "Misc",
        category: str = "misc",
        base_folder: Path | None = None,
        test_mode: bool = False,
        target_indexer_id: str | None = None,
        target_indexer_ids: list[str] | None = None,
        prefetched_dest_status: dict[str, str | None] | None = None,
        **_extra,
    ) -> int:
        captured["path"] = path
        captured["itype"] = itype
        captured["category"] = category
        captured["base_folder"] = base_folder
        return 1  # treated as "skipped" by caller

    monkeypatch.setattr(processing, "process_single", fake_process_single)

    processing.run_job(category="tv", limit=1, test_mode=True)

    assert captured["itype"] == "TV Episode"
    assert captured["category"] == "tv"

def test_process_single_uses_expected_submission_category(tmp_path, monkeypatch) -> None:
    import logic.processing as processing

    cases = [
        (
            "cached-anime-episode",
            "tv/Digimon.Tamers.S01E01.1080p.WEB-DL.mkv",
            False,
            "TV Episode",
            "tv",
            lambda name: True if "Digimon" in name else None,
            "anime",
        ),
        (
            "tv-pack-normalizes-to-tv",
            "tv/Show.Name.S01.1080p.WEB-DL",
            True,
            "TV Show",
            "tv",
            lambda _name: None,
            "tv",
        ),
    ]

    for case_name, relative_path, is_dir, itype, category, anime_lookup, expected_submission_category in cases:
        case_root = tmp_path / case_name
        path = case_root / relative_path
        if is_dir:
            _touch(path / "Show.Name.S01E01.1080p.WEB-DL.mkv", b"x" * 10)
            _touch(path / "Show.Name.S01E02.1080p.WEB-DL.mkv", b"x" * 10)
            target = path
        else:
            target = _touch(path, b"x" * 10)

        conf = _make_process_single_conf(case_root, include_script_dir=True)
        _configure_process_single_environment(monkeypatch, processing, conf)
        monkeypatch.setattr("logic.anime_cache.get_cached", anime_lookup)

        seen: dict[str, str] = {}

        def fake_submit_api(_name, _dest, _conf, **kwargs):
            seen["cat"] = kwargs["cat"]
            return SubmitResult(True, "success", "ok")

        monkeypatch.setattr(processing, "submit_api", fake_submit_api)

        result = processing.process_single(
            target,
            itype=itype,
            category=category,
            base_folder=case_root / "tv",
            test_mode=False,
        )

        assert result == 0, case_name
        assert seen["cat"] == expected_submission_category, case_name

def test_pending_bulk_preview_applies_exclusions_and_path_consolidation(tmp_path, monkeypatch) -> None:
    blocked_root = tmp_path / "qbittorrent"
    allowed_root = tmp_path / "ready"
    blocked_root.mkdir()
    allowed_root.mkdir()
    blocked_item = blocked_root / "Blocked.Release"
    allowed_parent = allowed_root / "Allowed.Release"
    allowed_child = allowed_parent / "Allowed.Release.2026.mkv"
    blocked_item.mkdir()
    allowed_parent.mkdir()
    allowed_child.write_bytes(b"x")
    conf = SimpleNamespace(
        folder_paths=[
            {"path": str(blocked_root), "allow_bulk_selection": False},
            {"path": str(allowed_root), "allow_bulk_selection": True},
        ]
    )
    captured: dict[str, object] = {}

    async def fake_preview(items, **kwargs):
        captured["items"] = items
        captured["kwargs"] = kwargs
        return {
            "status": "preview",
            "summary": {"selected": len(items), "planned": len(items), "ready": len(items)},
            "destinations": {},
            "items": [],
        }

    monkeypatch.setattr(app_mod, "get_config", lambda: conf)
    monkeypatch.setattr(app_mod, "_preview_selected_items", fake_preview)
    req = app_mod.ForceUploadRequest(
        items=[
            {"path": str(blocked_item), "category": "movies"},
            {"path": str(allowed_parent), "category": "movies"},
            {"path": str(allowed_child), "category": "movies"},
        ],
        bulk_selection=True,
        indexer_id="geek",
    )

    result = _run_async(app_mod.preview_force_upload_items(req))

    assert captured["items"] == [{"path": str(allowed_child), "category": "movies"}]
    assert result["selection"] == {
        "matched": 3,
        "bulk_excluded": 1,
        "overlapping_paths": 1,
        "excluded_roots": [str(blocked_root.resolve())],
    }
    assert captured["kwargs"] == {
        "enable_duplicate_check": True,
        "test_mode": False,
        "indexer_id": "geek",
        "force": None,
    }

def test_build_pending_summary_supports_dynamic_categories() -> None:
    summary = app_mod._build_pending_summary(
        {
            "tv": [
                {"episode_count": 5, "indexers": {"geek": False, "planet": True}},
                {"episode_count": 2, "indexers": {"geek": True, "planet": False}},
            ],
            "movies": [{"name": "Movie.1", "indexers": {"geek": False, "planet": False}}],
            "misc": [
                {"name": "Misc.1", "indexers": {"geek": True, "planet": True}},
                {"name": "Misc.2", "indexers": {"geek": False, "planet": True}},
            ],
            "anime": [
                {"name": "A", "indexers": {"geek": True, "planet": True}},
                {"name": "B", "indexers": {"geek": False, "planet": True}},
                {"name": "C", "indexers": {"geek": True, "planet": False}},
            ],
            "external": [
                {"items": [{"name": "e1", "indexers": {"geek": False, "planet": True}}]},
                {
                    "items": [
                        {"name": "e2", "indexers": {"geek": True, "planet": False}},
                        {"name": "e3", "indexers": {"geek": True, "planet": True}},
                    ]
                },
            ],
        },
        [
            {"id": "geek", "backfill": False},
            {"id": "planet", "backfill": False},
        ],
    )

    assert summary["tv_shows"] == 0
    assert summary["tv_episodes"] == 0
    assert summary["movies"] == 1
    assert summary["misc"] == 2
    assert summary["anime"] == 3
    assert summary["external"] == 3
    assert summary["item_total"] == 9
    assert summary["red_indexers"] == 2
    assert summary["task_total"] == 7
    assert summary["total"] == 7

def test_pending_cache_requests_refresh_without_blocking(monkeypatch) -> None:
    cases = [
        (
            "items-cold-non-blocking",
            "get_pending_items",
            {},
            {"snapshot": None, "snapshot_ts": 0.0, "ready": False, "refreshing": False, "last_error": None},
            "items-cold",
            None,
            True,
        ),
        (
            "items-refresh-is-async",
            "get_pending_items",
            {"refresh": True},
            {
                "snapshot": _make_pending_snapshot(),
                "snapshot_ts": 123.0,
                "ready": True,
                "refreshing": False,
                "last_error": None,
            },
            "items-refresh",
            True,
            None,
        ),
        (
            "summary-cold-refresh",
            "get_pending_summary",
            {},
            {"snapshot": None, "snapshot_ts": 0.0, "ready": False, "refreshing": False, "last_error": None},
            "summary-cold",
            None,
            None,
        ),
    ]

    monkeypatch.setattr(
        app_mod, "_scan_pending_all", lambda: (_ for _ in ()).throw(RuntimeError("request path should not scan"))
    )

    for case_name, fn_name, kwargs, state, expected_reason, expect_stale, expect_refreshing in cases:
        fake = _make_pending_index_manager([state])
        monkeypatch.setattr(app_mod, "_pending_index", fake)

        result = getattr(app_mod, fn_name)(**kwargs)

        assert result["ready"] is state["ready"], case_name
        assert fake.reasons == [expected_reason], case_name
        if expect_stale is not None:
            assert result["stale"] is expect_stale, case_name
        if expect_refreshing is not None:
            assert result["refreshing"] is expect_refreshing, case_name

def test_pending_cache_waits_for_snapshot(monkeypatch) -> None:
    cases = [
        (
            "items-waits-for-snapshot",
            "get_pending_items",
            [
                {"snapshot": None, "snapshot_ts": 0.0, "ready": False, "refreshing": False, "last_error": None},
                {"snapshot": None, "snapshot_ts": 0.0, "ready": False, "refreshing": False, "last_error": None},
                {
                    "snapshot": _make_pending_snapshot(
                        items={"tv": [], "movies": [{"name": "Movie.One"}], "misc": [], "external": []},
                        summary={"tv_shows": 0, "tv_episodes": 0, "movies": 1, "misc": 0, "external": 0, "total": 1},
                        cached_at=123.0,
                    ),
                    "snapshot_ts": 123.0,
                    "ready": True,
                    "refreshing": False,
                    "last_error": None,
                },
            ],
            123.0,
        ),
        (
            "summary-waits-for-snapshot",
            "get_pending_summary",
            [
                {"snapshot": None, "snapshot_ts": 0.0, "ready": False, "refreshing": False, "last_error": None},
                {
                    "snapshot": _make_pending_snapshot(
                        items={"tv": [], "movies": [{"name": "Movie.One"}], "misc": [], "external": []},
                        summary={"tv_shows": 0, "tv_episodes": 0, "movies": 1, "misc": 0, "external": 0, "total": 1},
                        cached_at=456.0,
                    ),
                    "snapshot_ts": 456.0,
                    "ready": True,
                    "refreshing": False,
                    "last_error": None,
                },
            ],
            456.0,
        ),
    ]

    monkeypatch.setattr(app_mod.time, "sleep", lambda _s: None)

    for case_name, fn_name, states, cached_at in cases:
        monkeypatch.setattr(app_mod, "_pending_index", _make_pending_index_manager(states))

        result = getattr(app_mod, fn_name)()

        assert result["ready"] is True, case_name
        assert result["summary"]["movies"] == 1, case_name
        assert result["cached_at"] == cached_at, case_name

def test_pending_items_returns_small_not_modified_response(monkeypatch) -> None:
    snapshot = _make_pending_snapshot(
        items={
            "tv": [],
            "movies": [{"name": "Movie.One", "path": "/movies/Movie.One"}],
            "misc": [],
            "external": [],
        },
        cached_at=456.0,
    )
    monkeypatch.setattr(
        app_mod,
        "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 456.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )

    result = app_mod.get_pending_items(known_cached_at=456.0)

    assert result == {
        "not_modified": True,
        "cached_at": 456.0,
        "ready": True,
        "refreshing": False,
        "anime_detecting": app_mod._anime_check_inflight,
    }

def test_pending_items_slims_children_without_mutating_cached_snapshot(monkeypatch) -> None:
    child = {"name": "Episode.01.mkv", "key": "ext:tv:Show/Episode.01.mkv"}
    top_level = {
        "name": "Show",
        "key": "ext:tv:Show",
        "path": "/tv/Show",
        "children": [child],
        "files": [child],
    }
    snapshot = _make_pending_snapshot(
        items={
            "tv": [],
            "movies": [],
            "misc": [],
            "external": [{"key": "/tv", "items": [top_level]}],
        }
    )
    monkeypatch.setattr(
        app_mod,
        "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 123.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )
    monkeypatch.setattr(app_mod, "get_config", lambda: SimpleNamespace(enable_anime_checking=False))

    result = app_mod.get_pending_items()
    returned = result["items"]["external"][0]["items"][0]

    assert returned["children"] == []
    assert returned["files"] == []
    assert returned["child_count"] == 1
    assert top_level["children"] == [child]
    assert top_level["files"] == [child]

def test_pending_snapshot_compacts_descendants_and_preserves_search() -> None:
    from logic import pending_snapshot as pending_snapshot_mod

    children = [
        {
            "name": f"Rare.Show.S01E{number:03d}.1080p.WEB-DL.mkv",
            "key": f"ext:tv:Rare.Show/Rare.Show.S01E{number:03d}.1080p.WEB-DL.mkv",
            "path": f"/tv/Rare.Show/Rare.Show.S01E{number:03d}.1080p.WEB-DL.mkv",
            "size": 1_000_000,
            "is_dir": False,
            "indexers": {"idx": number % 2 == 0},
            "completed": number % 2 == 0,
            "children": [],
            "files": [],
        }
        for number in range(1, 201)
    ]
    top = {
        "name": "Rare.Show",
        "key": "ext:tv:Rare.Show",
        "path": "/tv/Rare.Show",
        "size": 200_000_000,
        "children": children,
        "files": children,
        "child_count": len(children),
        "indexers": {"idx": False},
        "completed": False,
    }

    def retained_size(value, seen=None):
        seen = seen or set()
        value_id = id(value)
        if value_id in seen:
            return 0
        seen.add(value_id)
        total = sys.getsizeof(value)
        if isinstance(value, dict):
            total += sum(retained_size(key, seen) + retained_size(item, seen) for key, item in value.items())
        elif isinstance(value, (list, tuple, set)):
            total += sum(retained_size(item, seen) for item in value)
        return total

    before = retained_size(top)
    search_index, metadata_blob = pending_snapshot_mod._compact_external_groups([{"items": [top]}])
    after = retained_size(top) + retained_size(search_index) + retained_size(metadata_blob)

    assert top["children"] == []
    assert top["files"] == []
    assert top["child_count"] == 200
    assert "Rare.Show.S01E173" in search_index[top["key"]]
    assert after < before * 0.35

    data = _make_pending_snapshot(
        items={"tv": [], "movies": [], "misc": [], "external": [{"items": [top]}]},
        indexers=[{"id": "idx", "backfill": True}],
        _external_search_index=search_index,
        _external_metadata_zlib=metadata_blob,
    )
    filtered = pending_snapshot_mod.filter_pending_snapshot(data, "S01E173", "all")
    assert filtered["items"]["external"][0]["items"] == [top]

def test_pending_children_rejects_paths_outside_snapshot_root(tmp_path, monkeypatch) -> None:
    from logic import pending_snapshot as pending_snapshot_mod

    root = tmp_path / "root"
    top_path = root / "Release"
    outside = tmp_path / "outside"
    top_path.mkdir(parents=True)
    outside.mkdir()
    data = _make_pending_snapshot(
        items={
            "tv": [],
            "movies": [],
            "misc": [],
            "external": [
                {
                    "folder_name": "root",
                    "folder_path": str(root),
                    "items": [{"key": "ext:root:Release", "path": str(top_path)}],
                }
            ],
        }
    )
    monkeypatch.setattr(
        pending_snapshot_mod,
        "build_external_children_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("outside path was accepted")),
    )

    assert pending_snapshot_mod.build_external_children_for_request(
        data,
        "ext:root:../outside",
        str(outside),
    ) == {"children": [], "child_count": 0}

def test_pending_items_never_serializes_private_indexes(monkeypatch) -> None:
    snapshot = _make_pending_snapshot(
        _external_search_index={"ext:root:item": "item"},
        _external_metadata_zlib=b"private",
    )
    monkeypatch.setattr(
        app_mod,
        "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 123.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )
    monkeypatch.setattr(app_mod, "get_config", lambda: SimpleNamespace(enable_anime_checking=False))

    result = app_mod.get_pending_items()

    assert "_external_search_index" not in result
    assert "_external_metadata_zlib" not in result

def test_pending_filter_tv_includes_external_tv_groups() -> None:
    data = {
        "items": {
            "tv": [],
            "movies": [],
            "misc": [],
            "external": [
                {
                    "source_category": "tv",
                    "folder_name": "tv",
                    "folder_path": "D:/watch/tv",
                    "items": [
                        {
                            "name": "Show.With.Ambiguous.Name",
                            "key": "ext:tv:Show.With.Ambiguous.Name",
                            "indexers": {},
                        }
                    ],
                },
                {
                    "source_category": "external",
                    "folder_name": "external",
                    "folder_path": "D:/watch/external",
                    "items": [
                        {
                            "name": "Detected.TV.Release",
                            "key": "ext:external:Detected.TV.Release",
                            "detected_category": "tv",
                            "indexers": {},
                        },
                        {
                            "name": "Movie.Release.2024",
                            "key": "ext:external:Movie.Release.2024",
                            "detected_category": "movies",
                            "indexers": {},
                        },
                    ],
                },
            ],
        },
        "indexers": [],
        "summary": {
            "tv_shows": 0,
            "tv_episodes": 0,
            "movies": 0,
            "misc": 0,
            "external": 3,
            "total": 3,
        },
        "cached_at": 123.0,
        "skip_files": {"enabled": False, "display_mode": "disabled"},
        "categories": [{"id": "tv", "label": "TV Shows"}],
    }

    result = app_mod._filter_pending(data, None, "tv", False)

    assert len(result["items"]["external"]) == 2
    assert result["items"]["external"][0]["items"][0]["name"] == "Show.With.Ambiguous.Name"
    assert result["items"]["external"][1]["items"] == [
        {
            "name": "Detected.TV.Release",
            "key": "ext:external:Detected.TV.Release",
            "detected_category": "tv",
            "indexers": {},
        }
    ]

def test_collect_anime_check_names_includes_tv_movies_and_external() -> None:
    data = {
        "items": {
            "tv": [{"name": "Frieren"}],
            "movies": [{"name": "Spirited.Away.2001.1080p.BluRay", "itype": "Movie"}],
            "misc": [{"name": "Non.Video.Item", "itype": "Misc"}],
            "anime": [{"name": "Your.Name.2016.1080p.BluRay", "itype": "Anime"}],
            "external": [
                {
                    "items": [
                        {"name": "Cowboy.Bebop.1998.COMPLETE", "itype": "TV Show"},
                        {"name": "Kiki.Delivery.Service.1989.1080p", "itype": "Movie"},
                        {"name": "Random.Track.flac", "itype": "Music"},
                    ]
                }
            ],
        }
    }

    names = app_mod._collect_anime_check_names(data)

    assert set(names) == {
        "Spirited.Away.2001.1080p.BluRay",
        "Your.Name.2016.1080p.BluRay",
        "Cowboy.Bebop.1998.COMPLETE",
        "Kiki.Delivery.Service.1989.1080p",
    }

def test_movie_name_detection_prefers_movie_classification(monkeypatch, tmp_path) -> None:
    import logic.anime_cache as anime_cache

    cases = [
        ("cached-anime-with-year", "Spirited.Away.2001.1080p.BluRay.x264", "movies", True),
        ("movie-year-parentheses", "The.Fugitive.(1993).mkv", "", False),
        ("movie-year-subtitle", "The.Fugitive.-.Director.Cut.(1993).mkv", "", False),
        ("movie-year-hdtv", "The.Fugitive.1993.1080i.HDTV.x264.mkv", "", False),
    ]

    for case_name, name, folder_hint, cached in cases:
        monkeypatch.setattr(anime_cache, "get_cached", lambda _name: cached)

        assert app_mod._classify_video_name(name, folder_hint) == "Movie", case_name

        movie = _touch(tmp_path / case_name / name)
        assert pending_scan.detect_auto_category(movie) == "movies", case_name
        assert pending_scan.detect_auto_itype(movie) == "Movie", case_name

def test_resolve_explicit_path_single_item_cases(tmp_path) -> None:
    cases = [
        ("epub-overrides-books", "Movies/Novel.epub", "movies", "books", "Ebook", "overrode"),
        (
            "anime-hint-rejected",
            "Anime/Archer.S01E01.1080p.WEB-DL.mkv",
            "anime",
            "tv",
            "TV Episode",
            "Jikan rejected anime hint",
        ),
        (
            "tv-episode-wins-over-movies-hint",
            "Movies/Show.Name.S01E01.1080p.WEB-DL.mkv",
            "movies",
            "tv",
            "TV Episode",
            None,
        ),
        ("s00-special-is-tv", "Show.Name.S00E03.Christmas.Special.1080p.WEB-DL.mkv", None, "tv", "TV Episode", None),
        ("anime-hint-does-not-force-movie", "Perfect.Blue.1997.1080p.BluRay.mkv", "anime", "movies", "Movie", None),
    ]

    for case_name, relative_path, category_hint, expected_category, expected_itype, override_contains in cases:
        target = _touch(tmp_path / case_name / Path(relative_path))
        kwargs = {"anime_lookup": lambda _name: False}
        if category_hint is not None:
            kwargs["category_hint"] = category_hint

        result = pending_scan.resolve_explicit_path(target, **kwargs)

        assert result.category == expected_category, case_name
        assert result.itype == expected_itype, case_name
        assert result.detection_method in {"File extension", "File scan"}, case_name
        assert result.queue_paths == (target,), case_name
        if override_contains:
            assert override_contains in result.override_note, case_name

def test_resolve_explicit_path_uses_jikan_for_anime_and_filters_extras(tmp_path) -> None:
    anime_dir = tmp_path / "Movies" / "Frieren"
    episode_one = _touch(anime_dir / "[SubsPlease] Frieren - 01 (1080p).mkv", b"a")
    episode_two = _touch(anime_dir / "[SubsPlease] Frieren - 02 (1080p).mkv", b"b")
    opening = _touch(anime_dir / "[SubsPlease] Frieren - NCOP (1080p).mkv", b"c")

    result = pending_scan.resolve_explicit_path(
        anime_dir,
        category_hint="tv",
        anime_lookup=lambda _name: True,
    )

    assert result.category == "anime"
    assert result.itype == "Anime"
    assert result.detection_method == "Jikan match"
    assert result.queue_paths == (episode_one, episode_two)
    assert _ignored_path_reasons(result) == [(opening, "No episode pattern")]
    assert "overrode" in result.override_note

def test_resolve_explicit_path_tv_pack_ignores_non_episode_files(tmp_path) -> None:
    season = tmp_path / "TV" / "Show.Name.S01"
    episode_one = _touch(season / "Show.Name.S01E01.1080p.WEB-DL.mkv", b"a")
    episode_two = _touch(season / "Show.Name.S01E02.1080p.WEB-DL.mkv", b"b")
    opening = _touch(season / "Show.Name.OP.1080p.WEB-DL.mkv", b"c")
    notes = _touch(season / "readme.txt", "hello")

    result = pending_scan.resolve_explicit_path(
        season,
        category_hint="tv",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Show"
    assert result.detection_method == "File scan"
    assert result.queue_paths == (episode_one, episode_two)
    assert _ignored_path_reasons(result) == [
        (opening, "No S##E## episode pattern"),
        (notes, "TV season pack extra/non-video content"),
    ]

def test_resolve_explicit_path_disc_content_is_flagged_and_ignored(tmp_path) -> None:
    release_dir = tmp_path / "Movie.Name.2024.COMPLETE.BluRay"
    stream = _touch(release_dir / "BDMV" / "00001.m2ts", b"a")
    index = _touch(release_dir / "BDMV" / "index.bdmv", b"b")

    result = pending_scan.resolve_explicit_path(release_dir, anime_lookup=lambda _name: False)

    assert result.category == "movies"
    assert result.itype == "Movie"
    assert result.detection_method == "Disc scan"
    assert result.queue_paths == ()
    assert result.content_flags == ("disc",)
    assert _ignored_path_reasons(result) == [
        (stream, "Disc content"),
        (index, "Disc content"),
    ]

def test_resolve_explicit_path_tv_disc_pack_stays_tv_when_anime_not_confirmed(tmp_path) -> None:
    season_dir = tmp_path / "Season 1"
    _touch(season_dir / "DISC_1" / "VIDEO_TS" / "VIDEO_TS.IFO", b"a")

    result = pending_scan.resolve_explicit_path(
        season_dir,
        category_hint="anime",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Show"
    assert result.detection_method == "Disc scan"
    assert result.content_flags == ("disc",)
    assert "did not confirm Anime" in result.override_note

def test_resolve_explicit_path_uses_series_signature_for_generic_seasonal_anime_folder(tmp_path) -> None:
    season_dir = tmp_path / "Seasonal Anime"
    episode = _touch(season_dir / "[SubsPlease] Frieren - 01 (1080p).mkv", b"x")

    result = pending_scan.resolve_explicit_path(
        season_dir,
        anime_lookup=lambda name: True if str(name).strip().lower() == "frieren" else None,
    )

    assert result.category == "anime"
    assert result.itype == "Anime"
    assert result.detection_method == "Jikan match"
    assert result.queue_paths == (episode,)

def test_resolve_explicit_path_detects_ebook_folder_and_ignores_sidecars(tmp_path) -> None:
    books_dir = tmp_path / "Library" / "Novel.Release"
    book = _touch(books_dir / "Novel.epub", b"x")
    cover = _touch(books_dir / "cover.jpg", b"y")
    readme = _touch(books_dir / "readme.txt", "hello")

    result = pending_scan.resolve_explicit_path(books_dir)

    assert result.category == "books"
    assert result.itype == "Ebook"
    assert result.detection_method == "File scan"
    assert result.queue_paths == (book,)
    assert _ignored_path_reasons(result) == [
        (cover, "Non-ebook folder content"),
        (readme, "Non-ebook folder content"),
    ]

def test_pending_scan_falls_back_to_misc_for_ambiguous_external_video(tmp_path) -> None:
    for case_name, root_name, release_name in (
        ("ambiguous-movie-like-video", "0--Movies", "Plain.Release.Name.1080p.BluRay.mkv"),
        ("ambiguous-tv-like-video", "TV", "Plain.Release.Name.1080p.WEB-DL.mkv"),
    ):
        root = tmp_path / case_name / root_name
        release = _touch(root / release_name, b"a")

        items = pending_scan.scan_folder_items(root, "external")

        assert len(items) == 1, case_name
        assert items[0].path == release, case_name
        assert items[0].category == "misc", case_name
        assert items[0].episode_paths == (), case_name

def test_pending_items_anime_check_start_behavior(monkeypatch) -> None:
    for case_name, cached_lookup_result, should_start in (
        ("uncached-titles-start-background-check", None, True),
        ("cached-titles-skip-background-check", False, False),
    ):
        started, snapshot = _run_pending_items_anime_check(monkeypatch, cached_lookup_result=cached_lookup_result)

        if should_start:
            assert len(started) == 1, case_name
            assert started[0][0] == app_mod._background_anime_check, case_name
            assert started[0][1] == (snapshot,), case_name
        else:
            assert started == [], case_name

def test_pending_scan_collects_tv_items_and_pack(tmp_path) -> None:
    tv_dir = tmp_path / "tv"
    season = tv_dir / "Show.S01"
    season.mkdir(parents=True)
    (season / "Show.S01E01.mkv").write_bytes(b"x")
    (season / "Show.S01E02.mkv").write_bytes(b"y")

    items = pending_scan.collect_category_scan_items(tv_dir, "tv", {".mkv"})

    rel_keys = {item.rel_key for item in items}
    assert "Show.S01/Show.S01E01.mkv" in rel_keys
    assert "Show.S01/Show.S01E02.mkv" in rel_keys
    assert "Show.S01" in rel_keys

    episode_count = sum(1 for item in items if item.is_episode)
    pack_count = sum(1 for item in items if not item.is_episode and item.rel_key == "Show.S01")
    assert episode_count == 2
    assert pack_count == 1

def test_upload_service_dashboard_summary_uses_pending_index_state(monkeypatch) -> None:
    from logic import services as services_mod

    cases = [
        (
            "uses-ready-snapshot",
            {
                "snapshot": {
                    "items": {
                        "tv": [
                            {
                                "name": "Show A",
                                "completed": False,
                                "indexers": {"idx1": False},
                                "seasons": [
                                    {
                                        "items": [
                                            {"itype": "TV Episode", "completed": False},
                                            {"itype": "TV Episode", "completed": True},
                                        ]
                                    }
                                ],
                            }
                        ],
                        "movies": [{"name": "Movie A", "completed": True, "indexers": {"idx1": True}}],
                        "external": [
                            {
                                "items": [
                                    {"name": "Ext Movie", "detected_category": "movies", "indexers": {"idx1": False}}
                                ]
                            }
                        ],
                    },
                    "indexers": [{"id": "idx1", "name": "Indexer 1", "backfill": False}],
                    "db_error": "db down",
                },
                "ready": True,
            },
            {
                "uploads": {"by_destination": {"idx1": {"success": 3, "failed": 1}}},
                "performance": {"avg_speed_bps": 123},
            },
            True,
            None,
        ),
        (
            "requests-refresh-when-cold",
            {"snapshot": None, "ready": False},
            {"uploads": {}, "performance": {}},
            False,
            "dashboard-summary",
        ),
    ]

    monkeypatch.setattr(
        services_mod, "get_config", lambda: SimpleNamespace(poster_name="Poster", ui_refresh_seconds=2, nntp_servers=[])
    )

    for case_name, manager_state, stats_payload, expected_ready, expected_refresh_reason in cases:
        pending_manager = _make_pending_index_manager([manager_state])
        service = _make_dashboard_summary_service()

        monkeypatch.setattr(services_mod, "get_pending_index_manager", lambda: pending_manager)
        monkeypatch.setattr(service, "get_statistics", lambda: stats_payload)

        summary = service.get_dashboard_summary()
        assert summary["summary_ready"] is expected_ready, case_name
        if expected_ready:
            assert summary["pending"]["tv"] == 1, case_name
            assert summary["pending"]["movies"] == 1, case_name
            assert summary["pending"]["movies_complete"] == 1, case_name
            assert summary["pending"]["tv_episodes_pending"] == 1, case_name
            assert summary["pending"]["tv_episodes_complete"] == 1, case_name
            assert summary["pending"]["total_tasks"] == 2, case_name
            assert summary["breakdown"]["tv"]["idx1"] == {"complete": 0, "pending": 1}, case_name
            assert summary["breakdown"]["movies"]["idx1"] == {"complete": 1, "pending": 1}, case_name
            assert summary["indexers"][0]["stats"] == {"success": 3, "failed": 1}, case_name
            assert summary["db_error"] == "db down", case_name
        else:
            assert pending_manager.reasons == [expected_refresh_reason], case_name

def test_scan_pending_snapshot_external_tree_avoids_recursive_size_rewalk(tmp_path, monkeypatch) -> None:
    from logic import pending_snapshot as pending_snapshot_mod

    external_dir = tmp_path / "external"
    release_dir = external_dir / "Release.Dir"
    nested_dir = release_dir / "CD1"
    nested_dir.mkdir(parents=True)
    (nested_dir / "part1.mkv").write_bytes(b"a" * 3)
    (release_dir / "sample.nfo").write_text("nfo", encoding="utf-8")

    conf = SimpleNamespace(folder_paths=[{"path": str(external_dir), "category": "external"}], skip_files=None)

    def fail_if_called(_path: Path) -> int:
        raise AssertionError("compute_size_uncached should not be used for fully scanned external trees")

    _configure_pending_snapshot_environment(
        monkeypatch,
        pending_snapshot_mod,
        conf,
        dashboard_data=({}, {}, {}),
        configured_folders=[("external", external_dir)],
        compute_size_uncached=fail_if_called,
    )

    payload = pending_snapshot_mod.scan_pending_snapshot()

    item = payload["items"]["external"][0]["items"][0]
    assert item["size"] == 6
    assert _pending_lazy_children(payload, item)[0]["size"] == 3
    assert payload["summary"]["external"] == 1

def test_scan_pending_snapshot_exposes_bulk_selection_policy(tmp_path, monkeypatch) -> None:
    from logic import pending_snapshot as pending_snapshot_mod

    external_dir = tmp_path / "qbittorrent"
    release_dir = external_dir / "Release.Dir"
    release_dir.mkdir(parents=True)
    (release_dir / "Movie.2026.1080p.WEB-DL.mkv").write_bytes(b"x")
    conf = SimpleNamespace(
        folder_paths=[
            {
                "path": str(external_dir),
                "category": "external",
                "allow_bulk_selection": False,
            }
        ],
        skip_files=None,
    )

    _configure_pending_snapshot_environment(
        monkeypatch,
        pending_snapshot_mod,
        conf,
        dashboard_data=(set(), {}, {}),
        configured_folders=[("external", external_dir)],
    )

    payload = pending_snapshot_mod.scan_pending_snapshot()

    assert payload["items"]["external"][0]["allow_bulk_selection"] is False

def test_scan_pending_snapshot_selectable_external_dir_keeps_direct_completion_state(tmp_path, monkeypatch) -> None:
    from logic import pending_snapshot as pending_snapshot_mod

    external_dir = tmp_path / "external"
    release_dir = external_dir / "Movie.Name.2026"
    release_dir.mkdir(parents=True)
    movie_file = release_dir / "Movie.Name.2026.1080p.mkv"
    movie_file.write_bytes(b"x" * 10)

    conf = SimpleNamespace(folder_paths=[{"path": str(external_dir), "category": "external"}], skip_files=None)
    indexer = SimpleNamespace(id="idx1", name="Indexer 1", color="#fff", icon="database", favicon_url=None)

    _configure_pending_snapshot_environment(
        monkeypatch,
        pending_snapshot_mod,
        conf,
        dashboard_data=(set(), {movie_file.name: {"idx1"}}, {}),
        configured_folders=[("external", external_dir)],
        indexers=[indexer],
        resolve_backfill=lambda _idx, _conf: True,
    )

    payload = pending_snapshot_mod.scan_pending_snapshot()

    item = payload["items"]["external"][0]["items"][0]
    assert item["is_dir"] is True
    assert item["auto_selectable"] is True
    assert item["completed"] is False
    assert item["indexers"] == {"idx1": False}
    children = _pending_lazy_children(payload, item)
    assert children[0]["completed"] is True
    assert children[0]["indexers"] == {"idx1": True}

def test_folder_monitor_trigger_uploads_respects_configured_category(monkeypatch, tmp_path) -> None:
    from logic import services as services_mod

    monitored_dir = tmp_path / "movies"
    monitored_dir.mkdir()
    release_dir = monitored_dir / "Release.Name"
    release_dir.mkdir()

    started = []

    class _FakeService:
        def start_path_jobs(self, grouped_paths, **kwargs):
            started.append((grouped_paths, kwargs))
            return [{"job_id": "job-1", "category": "movies", "paths": grouped_paths["movies"]}]

    monkeypatch.setattr(services_mod, "get_upload_service", lambda: _FakeService())
    monkeypatch.setattr(folder_monitor, "detect_auto_category", lambda _path: "tv")

    folder_monitor._trigger_uploads(str(monitored_dir), {release_dir.name: "movies"})

    assert started == [
        (
            {"movies": [str(release_dir)]},
            {"reuse_running": False, "source": "folder-monitor"},
        )
    ]

def test_scan_pending_all_classifies_single_item_scenarios(monkeypatch, tmp_path) -> None:
    cases = [
        {
            "name": "cached-anime-tv-show",
            "folder_category": "tv",
            "root_name": "tv",
            "location": "external",
            "files": {"Digimon Tamers/Digimon.Tamers.S01E01.mkv": b"x"},
            "anime_match_name": "Digimon Tamers",
            "expected": {
                "itype": "Anime",
                "detected_category": "anime",
                "detection_method": "Jikan match",
            },
            "expected_child_name": "Digimon.Tamers.S01E01.mkv",
        },
        {
            "name": "epub-file-is-book",
            "folder_category": "external",
            "root_name": "external-book-file",
            "location": "external",
            "files": {"Novel.epub": b"x"},
            "anime_match_name": None,
            "expected": {
                "itype": "Ebook",
                "detected_category": "books",
                "detection_method": "File extension",
            },
            "expected_child_name": None,
        },
        {
            "name": "tv-episode-inside-movies-root",
            "folder_category": "movies",
            "root_name": "Movies",
            "location": "movies",
            "files": {"Archer.S01E01.1080p.WEB-DL.mkv": b"x"},
            "anime_match_name": None,
            "expected": {
                "itype": "TV Episode",
                "detected_category": "tv",
                "detection_method": "File scan",
            },
            "expected_child_name": None,
        },
        {
            "name": "unknown-external-category-blank",
            "folder_category": "external",
            "root_name": "external-unknown",
            "location": "external",
            "files": {"Untyped.Release/readme.txt": "hello"},
            "anime_match_name": None,
            "expected": {"itype": "Misc", "detected_category": ""},
            "expected_child_name": None,
        },
    ]

    for case in cases:
        folder_path = tmp_path / case["root_name"]
        for relative_path, content in case["files"].items():
            _touch(folder_path / relative_path, content)

        anime_match_name = case["anime_match_name"]
        anime_cache_lookup = (
            (lambda name: True if name == anime_match_name else None)
            if anime_match_name is not None
            else (lambda _name: False)
        )
        result = _configure_pending_scan_all(
            monkeypatch,
            case["folder_category"],
            folder_path,
            anime_cache_lookup=anime_cache_lookup,
        )

        top_item = _get_pending_top_item(result, case["location"])
        for key, value in case["expected"].items():
            assert top_item[key] == value, case["name"]
        expected_child_name = case["expected_child_name"]
        if expected_child_name is not None:
            assert _pending_lazy_children(result, top_item)[0]["name"] == expected_child_name, case["name"]

def test_scan_pending_all_marks_anime_extras_ignored_for_auto_select(monkeypatch, tmp_path) -> None:
    anime_dir = tmp_path / "Anime"
    show_dir = anime_dir / "Frieren"
    episode = _touch(show_dir / "[SubsPlease] Frieren - 01 (1080p).mkv", b"x")
    extra = _touch(show_dir / "[SubsPlease] Frieren - NCOP (1080p).mkv", b"x")

    result = _configure_pending_scan_all(
        monkeypatch,
        "external",
        anime_dir,
        anime_cache_lookup=lambda name: True if "Frieren" in name else None,
    )

    top_item = result["items"]["external"][0]["items"][0]
    child_map = {child["name"]: child for child in _pending_lazy_children(result, top_item)}

    assert top_item["detected_category"] == "anime"
    assert top_item["detection_method"] == "Jikan match"
    assert child_map[episode.name]["auto_selectable"] is True
    assert child_map[extra.name]["auto_select_ignored"] is True
    assert child_map[extra.name]["auto_select_reason"] == "No episode pattern"

def test_scan_pending_all_ignored_extra_files_do_not_block_pack_completion(monkeypatch, tmp_path) -> None:
    ext_dir = tmp_path / "TV"
    season = ext_dir / "Show.Name.S01"
    _touch(season / "Show.Name.S01E01.1080p.WEB-DL.mkv", b"x")
    extra = _touch(season / "Show.Name.Trailer.1080p.WEB-DL.mkv", b"x")
    note = _touch(season / "readme.txt", "hello")

    class _Indexer:
        id = "idx1"
        name = "Indexer 1"
        color = ""
        icon = ""
        favicon_url = ""
        backfill = False

    result = _configure_pending_scan_all(
        monkeypatch,
        "external",
        ext_dir,
        dashboard_data=(
            set(),
            {
                "Show.Name.S01/Show.Name.S01E01.1080p.WEB-DL.mkv": {"idx1"},
                "Show.Name.S01E01.1080p.WEB-DL.mkv": {"idx1"},
            },
            {},
        ),
        indexers=[_Indexer()],
    )

    top_item = result["items"]["external"][0]["items"][0]
    child_map = {child["name"]: child for child in _pending_lazy_children(result, top_item)}

    assert child_map[extra.name]["auto_select_ignored"] is True
    assert child_map[extra.name]["skipped"] is True
    assert child_map[extra.name]["completed"] is True
    assert child_map[note.name]["auto_select_ignored"] is True
    assert child_map[note.name]["completed"] is True
    assert top_item["completed"] is True
    assert top_item["indexers"]["idx1"] is True

def test_resolve_submission_category_cases(tmp_path) -> None:
    import logic.processing as processing

    cases = [
        ("rejects-ambiguous-video-misc", "Untitled.Release.mkv", "misc", "Misc", None, "cannot be submitted as Misc"),
        ("preserves-explicit-tv", "Show.Name.S00E01.1080p.WEB-DL.mkv", "tv", "Misc", "tv", None),
        ("preserves-explicit-anime", "Anime.Name.S01E01.1080p.WEB-DL.mkv", "anime", "TV Episode", "anime", None),
        (
            "rejects-invalid-explicit-category",
            "Show.Name.S01E01.1080p.WEB-DL.mkv",
            "external",
            "TV Episode",
            None,
            "explicit category 'external' is invalid",
        ),
    ]

    for case_name, filename, explicit_category, detected_itype, expected, error_match in cases:
        target = tmp_path / filename
        target.write_bytes(b"x")

        if error_match is not None:
            with pytest.raises(ValueError, match=error_match):
                processing._resolve_submission_category(target, explicit_category, detected_itype)
            continue

        assert processing._resolve_submission_category(target, explicit_category, detected_itype) == expected, case_name

def test_dashboard_snapshot_rows_skip_external_items_without_category() -> None:
    from logic import services as services_mod

    snapshot = {
        "items": {
            "movies": [{"name": "Movie.One"}],
            "external": [
                {
                    "items": [
                        {"name": "Unknown.One", "detected_category": ""},
                        {"name": "Show.One", "detected_category": "tv"},
                    ]
                }
            ],
        }
    }

    rows = services_mod.UploadService._iter_dashboard_snapshot_rows(snapshot)

    assert rows == [
        ("movies", {"name": "Movie.One"}),
        ("tv", {"name": "Show.One", "detected_category": "tv"}),
    ]

def test_scan_pending_all_external_sizes_recurse_full_depth(monkeypatch, tmp_path) -> None:
    ext_dir = tmp_path / "external"
    video_ts = ext_dir / "Ozzy & Drix (2002-2003) 3xDVD9 NTSC-CultFilms" / "DISC_1" / "VIDEO_TS"
    _touch(video_ts / "VIDEO_TS.IFO", b"1234567890")
    _touch(video_ts / "VTS_01_1.VOB", b"abcdefghijklmno")

    result = _configure_pending_scan_all(monkeypatch, "external", ext_dir)

    external_groups = result["items"]["external"]
    assert len(external_groups) == 1

    top_item = external_groups[0]["items"][0]
    disc_item = _pending_lazy_children(result, top_item)[0]
    video_ts_item = _pending_lazy_children(result, disc_item)[0]
    vob_item = _pending_lazy_children(result, video_ts_item)[1]
    expected_size = 25

    assert top_item["size"] == expected_size
    assert disc_item["size"] == expected_size
    assert video_ts_item["size"] == expected_size
    assert vob_item["name"] == "VTS_01_1.VOB"
    assert vob_item["size"] == 15
    assert top_item["itype"] == "Movie"
    assert top_item["detected_category"] == "movies"
    assert top_item["detection_method"] == "Disc scan"
    assert top_item["detection_flags"] == ["disc"]
    assert vob_item["auto_select_ignored"] is True
    assert vob_item["completed"] is True
    assert top_item["completed"] is True

def test_scan_pending_all_marks_ebook_folder_and_ignores_sidecars(monkeypatch, tmp_path) -> None:
    ext_dir = tmp_path / "Books"
    folder = ext_dir / "Novel.Release"
    book = _touch(folder / "Novel.epub", b"x")
    cover = _touch(folder / "cover.jpg", b"y")

    result = _configure_pending_scan_all(monkeypatch, "external", ext_dir)

    top_item = _get_pending_top_item(result, "external")
    child_map = {child["name"]: child for child in _pending_lazy_children(result, top_item)}

    assert top_item["itype"] == "Ebook"
    assert top_item["detected_category"] == "books"
    assert top_item["detection_method"] == "File scan"
    assert child_map[book.name]["auto_selectable"] is True
    assert child_map[cover.name]["auto_select_ignored"] is True
    assert child_map[cover.name]["completed"] is True

def test_pending_scan_category_folder_filtering(tmp_path) -> None:
    movies = tmp_path / "movies"
    ext = tmp_path / "external"
    implicit_ext = tmp_path / "implicit-external"
    movies.mkdir()
    ext.mkdir()
    implicit_ext.mkdir()

    class _Conf:
        folder_paths = [
            {"category": "movies", "path": str(movies)},
            {"category": "external", "path": str(ext)},
            {"path": str(implicit_ext)},
            {"category": "tv", "path": str(tmp_path / "missing-tv")},
        ]

    categories = pending_scan.get_configured_category_folders(_Conf(), include_external=False, must_exist=True)
    assert categories == [("movies", movies)]

    all_categories = pending_scan.get_configured_category_folders(_Conf(), include_external=True, must_exist=False)
    assert all_categories == [
        ("movies", movies),
        ("external", ext),
        ("external", implicit_ext),
        ("tv", tmp_path / "missing-tv"),
    ]

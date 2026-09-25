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
    monkeypatch.setattr(db_queue_items, "db_remove_queue_items", lambda item_ids: len(item_ids))
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
    from logic.classify.hints import infer_folder_category_hint

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
        monkeypatch.setattr("logic.classify.anime.get_cached", anime_lookup)

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

def test_pending_bulk_selection_applies_exclusions_and_path_consolidation(tmp_path) -> None:
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
    items = [
        {"path": str(blocked_item), "category": "movies"},
        {"path": str(allowed_parent), "category": "movies"},
        {"path": str(allowed_child), "category": "movies"},
    ]

    selected_items, excluded_count = deps_api._filter_bulk_selectable_items(items, conf)
    collapsed_items = pending_api._collapse_force_upload_items(selected_items)

    assert collapsed_items == [{"path": str(allowed_child), "category": "movies"}]
    assert excluded_count == 1
    assert len(selected_items) - len(collapsed_items) == 1
    assert [str(root) for root in deps_api._bulk_selection_excluded_roots(conf)] == [str(blocked_root.resolve())]

def test_build_pending_summary_supports_dynamic_categories() -> None:
    summary = pending_view.build_pending_summary(
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
        pending_api, "_scan_pending_all", lambda: (_ for _ in ()).throw(RuntimeError("request path should not scan"))
    )

    for case_name, fn_name, kwargs, state, expected_reason, expect_stale, expect_refreshing in cases:
        fake = _make_pending_index_manager([state])
        monkeypatch.setattr(pending_api, "_pending_index", fake)

        result = getattr(pending_api, fn_name)(**kwargs)

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

    monkeypatch.setattr(time, "sleep", lambda _s: None)

    for case_name, fn_name, states, cached_at in cases:
        monkeypatch.setattr(pending_api, "_pending_index", _make_pending_index_manager(states))

        result = getattr(pending_api, fn_name)()

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
        pending_api, "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 456.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )

    result = pending_api.get_pending_items(known_cached_at=456.0)

    assert result == {
        "not_modified": True,
        "cached_at": 456.0,
        "ready": True,
        "refreshing": False,
        "anime_detecting": pending_api._anime_check_inflight,
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
        pending_api, "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 123.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )
    patch_hit(monkeypatch, pending_api, "get_config", lambda: SimpleNamespace(enable_anime_checking=False))

    result = pending_api.get_pending_items()
    returned = result["items"]["external"][0]["items"][0]

    assert returned["children"] == []
    assert returned["files"] == []
    assert returned["child_count"] == 1
    assert top_level["children"] == [child]
    assert top_level["files"] == [child]

def test_pending_snapshot_compacts_descendants_and_preserves_search() -> None:
    from logic.pending import view as pending_view

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
    search_index, metadata_blob = pending_view._compact_external_groups([{"items": [top]}])
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
    filtered = pending_view.filter_pending_snapshot(data, "S01E173", "all")
    assert filtered["items"]["external"][0]["items"] == [top]

def test_pending_children_rejects_paths_outside_snapshot_root(tmp_path, monkeypatch) -> None:
    from logic.pending import children as pending_children

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
        pending_children,
        "build_external_children_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("outside path was accepted")),
    )

    assert pending_children.build_external_children_for_request(
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
        pending_api, "_pending_index",
        _make_pending_index_manager(
            [{"snapshot": snapshot, "snapshot_ts": 123.0, "ready": True, "refreshing": False, "last_error": None}]
        ),
    )
    patch_hit(monkeypatch, pending_api, "get_config", lambda: SimpleNamespace(enable_anime_checking=False))

    result = pending_api.get_pending_items()

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

    result = pending_view.filter_pending_snapshot(data, None, "tv", False)

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

    names = pending_index.collect_anime_check_names(data)

    assert set(names) == {
        "Frieren",
        "Spirited.Away.2001.1080p.BluRay",
        "Your.Name.2016.1080p.BluRay",
        "Cowboy.Bebop.1998.COMPLETE",
        "Kiki.Delivery.Service.1989.1080p",
    }

def test_movie_name_detection_prefers_movie_classification(monkeypatch, tmp_path) -> None:
    import logic.classify.anime as anime_cache

    cases = [
        ("detector-positive-overrides-year", "Animated.Feature.2001.1080p.BluRay.x264", "movies", True, "Anime", "anime"),
        ("movie-year-parentheses", "Feature.Title.(1993).mkv", "", False, "Movie", "movies"),
        ("movie-year-subtitle", "Feature.Title.-.Director.Cut.(1993).mkv", "", False, "Movie", "movies"),
        ("movie-year-hdtv", "Feature.Title.1993.1080i.HDTV.x264.mkv", "", False, "Movie", "movies"),
    ]

    for case_name, name, folder_hint, cached, expected_itype, expected_category in cases:
        monkeypatch.setattr(anime_cache, "get_cached", lambda _name: cached)

        assert classify_names.classify_video_name(name, folder_hint, anime_lookup=anime_cached_lookup) == expected_itype, case_name

        movie = _touch(tmp_path / case_name / name)
        assert classify_content.detect_auto_category(movie) == expected_category, case_name
        assert classify_content.detect_auto_itype(movie) == expected_itype, case_name

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

        result = classify_explicit.resolve_explicit_path(target, **kwargs)

        assert result.category == expected_category, case_name
        assert result.itype == expected_itype, case_name
        assert result.detection_method in {
            "File extension",
            "File scan",
            "Episode pattern",
            "Movie pattern",
        }, case_name
        assert result.queue_paths == (target,), case_name
        if override_contains:
            assert override_contains in result.override_note, case_name


def test_pokemon_anime_hint_survives_accent_and_source_tokens(tmp_path) -> None:
    anime_dir = tmp_path / "Anime" / "0-Pokemon Horizon - Singles"
    episode_names = [
        "Pokemon.S20E45.From.So.Far.Away.Part.2.1080p.WEBRip.10bit.EAC3.2.0.x265-iVy.mkv",
        "Pokémon.S20E112.Mega.Evolution.Roy.1080p.WEBRip.10bit.EAC3.2.0.x265-iVy.mkv",
    ]
    episodes = tuple(_touch(anime_dir / name) for name in episode_names)

    detector_queries = []

    def detector(name):
        detector_queries.append(name)
        return True

    result = classify_explicit.resolve_explicit_path(
        anime_dir,
        category_hint="anime",
        anime_lookup=detector,
    )

    assert result.category == "anime"
    assert result.itype == "Anime"
    assert result.queue_paths == episodes
    assert any("pokemon" in query.casefold() for query in detector_queries)
    for episode in episodes:
        assert classify_content.detect_auto_category(
            episode,
            folder_category_hint="anime",
            anime_lookup=detector,
        ) == "anime"
        assert classify_content.detect_auto_itype(
            episode,
            folder_category_hint="anime",
            anime_lookup=detector,
        ) == "Anime"


def test_archer_remains_western_tv_even_under_anime_hint(tmp_path) -> None:
    episode = _touch(tmp_path / "Anime" / "Archer.S01E01.1080p.WEB-DL.mkv")

    result = classify_explicit.resolve_explicit_path(
        episode,
        category_hint="anime",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Episode"
    assert classify_names.classify_video_name(
        episode.name,
        "anime",
        anime_lookup=lambda _name: False,
    ) == "TV Show"


def test_detector_result_precedes_generic_episode_shape(monkeypatch) -> None:

    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: True)

    release_name = "Any.Series.S12E34.1080p.WEB-DL"

    assert classify_names.classify_video_name(
        release_name,
        anime_lookup=lambda _name: True,
    ) == "Anime"


def test_detector_result_precedes_generic_movie_year(tmp_path) -> None:
    release = _touch(tmp_path / "Animated.Feature.2001.1080p.BluRay.mkv")

    result = classify_explicit.resolve_explicit_path(
        release,
        category_hint="movies",
        anime_lookup=lambda _name: True,
    )

    assert result.category == "anime"
    assert result.itype == "Anime"
    assert result.detection_method == "Jikan match"


def test_production_classifier_has_no_title_specific_exception_tables() -> None:
    sources = {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "logic").rglob("*.py"))
    }
    assert "logic/classify/names.py" in sources

    for relative, source in sources.items():
        assert "_KNOWN_ANIME_TITLE" not in source, relative
        assert "_KNOWN_TV_TITLE" not in source, relative
        assert "_KNOWN_MOVIE_TITLE" not in source, relative
        assert "_KNOWN_NON_ANIME_TV_TITLE" not in source, relative
        assert "looks_like_known_" not in source, relative
        assert "pokemon|pocket monsters|horizons" not in source, relative


def test_air_date_episodes_are_tv_not_movies() -> None:
    """A YYYY.MM.DD stamp is an episode air date, never a movie.

    has_clear_movie_year() matches the YYYY of the date itself and returned
    "Movie" before any TV pattern was consulted, so every daily show, news and
    sport broadcast was filed as a movie.
    """

    episodes = [
        "The.Daily.Show.2024.03.11.Jon.Stewart.720p.WEB",
        "Jimmy.Kimmel.2023.11.02.1080p.WEB.h264",
        "Show.Name.2020.12.31.Guest.720p.HDTV",
    ]
    for name in episodes:
        assert classify_names.classify_video_name(name) == "TV Show", name
        # The dashboard path must agree with the scanner path.
        assert classify_names.classify_video_name(name, anime_lookup=anime_cached_lookup) == "TV Show", name

    # A bare release year, a title that is a number, and an impossible
    # month/day must all still read as movies.
    movies = [
        "The.Matrix.1999.1080p.BluRay",
        "Blade.Runner.2049.2017.2160p.BluRay",
        "1917.2019.1080p.BluRay",
        "Movie.Name.2019.13.45.1080p",
    ]
    for name in movies:
        assert classify_names.classify_video_name(name) == "Movie", name

def test_season_series_tokens_do_not_swallow_resolution_or_year_digits() -> None:
    """Season/Series must be followed by a standalone number, not a prefix of one.

    Without a trailing (?!\\d) guard the pattern matched "Season.10" inside
    "The.Last.Season.1080p" and "SERIES.10" inside "MINISERIES.1080p", so movies
    whose titles end in Season/Series were classified as TV.
    """
    cases = [
        ("The.Last.Season.1080p.WEB-DL", "Misc"),
        ("Silent.Season.2160p.BluRay", "Misc"),
        ("Some.Miniseries.1080p.x264", "Misc"),
        ("The.Last.Season.2014.1080p.WEB-DL", "Movie"),
    ]
    for name, expected in cases:
        assert classify_names.classify_video_name(name) == expected, name

    # Genuine season/series markers must still be detected.
    shows = [
        "Poker.Face.Season.2.1080p.WEB",
        "Show.Name.Season.10.1080p.WEB",
        "Doctor.Who.Series.4.1080p.BluRay",
    ]
    for name in shows:
        assert classify_names.classify_video_name(name) == "TV Show", name

def test_anime_cache_key_normalizes_latin_diacritics() -> None:
    from logic.classify import anime as anime_cache

    assert anime_cache._cache_key("Pokémon") == anime_cache._cache_key("Pokemon")
    assert anime_cache._title_matches("Pokémon", "Pokemon") is True


def test_two_word_translated_anime_match_respects_release_year(monkeypatch, tmp_path) -> None:
    from logic.classify import anime as anime_cache

    response_data = {
        "data": [
            {
                "title": "Koukaku Kidoutai",
                "title_english": "Ghost in the Shell",
                "title_japanese": "攻殻機動隊",
                "titles": [
                    {"type": "English", "title": "Ghost in the Shell"},
                    {"type": "Default", "title": "Koukaku Kidoutai"},
                ],
                "score": 8.28,
                "members": 1_100_000,
                "year": 1995,
                "aired": {"from": "1995-11-18T00:00:00+00:00"},
                "type": "Movie",
            }
        ]
    }
    response = SimpleNamespace(status_code=200, json=lambda: response_data)
    monkeypatch.setattr(anime_cache, "_cache", {})
    monkeypatch.setattr(anime_cache, "_cache_loaded", True)
    monkeypatch.setattr(anime_cache, "_cache_path", tmp_path / "anime.json")
    monkeypatch.setattr(anime_cache, "_wait_for_slot", lambda: True)
    monkeypatch.setattr(anime_cache, "_record_request", lambda: None)
    monkeypatch.setattr(anime_cache.requests, "get", lambda *args, **kwargs: response)

    live_action = "Ghost.in.the.Shell.2017.1080p.BluRay.x264-GROUP"
    original_anime = "Ghost.in.the.Shell.1995.1080p.BluRay.x264-GROUP"

    assert anime_cache.is_anime(live_action) is False
    assert anime_cache.get_cached(live_action) is False
    assert anime_cache.is_anime(original_anime) is True
    assert anime_cache.get_cached(original_anime) is True
    assert anime_cache.set_cached("Ghost in the Shell", True) is True
    assert classify_names.classify_video_name(
        live_action,
        anime_lookup=anime_cache.get_cached,
    ) == "Movie"
    release_file = _touch(tmp_path / f"{live_action}.mkv", b"x")
    assert classify_content.detect_auto_category(
        release_file,
        anime_lookup=anime_cache.get_cached,
    ) == "movies"


def test_jikan_entry_year_uses_aired_date_when_year_is_missing() -> None:
    from logic.classify import anime as anime_cache

    entry = {"year": None, "aired": {"from": "1995-11-18T00:00:00+00:00"}}

    assert anime_cache._jikan_entry_year(entry) == 1995


def test_anime_cache_correction_can_flip_and_invalidate_one_release(monkeypatch, tmp_path) -> None:
    from logic.classify import anime as anime_cache

    cache_path = tmp_path / "anime.json"
    monkeypatch.setattr(anime_cache, "_cache", {})
    monkeypatch.setattr(anime_cache, "_cache_loaded", True)
    monkeypatch.setattr(anime_cache, "_cache_path", cache_path)

    release = "Ghost.in.the.Shell.2017.1080p.BluRay"
    original = "Ghost.in.the.Shell.1995.1080p.BluRay"

    assert anime_cache.set_cached(release, True) is True
    assert anime_cache.get_cached(release) is True
    assert anime_cache.get_cached(original) is None
    assert anime_cache.set_cached(release, False) is True
    assert anime_cache.get_cached(release) is False
    assert anime_cache.invalidate(release) is True
    assert anime_cache.get_cached(release) is None
    assert anime_cache.invalidate(release) is False
    assert cache_path.exists()


def test_explicit_user_category_overrides_cached_anime_verdict() -> None:
    name = "Ghost.in.the.Shell.2017.1080p.BluRay"

    assert classify_names.classify_video_name(
        name,
        anime_lookup=lambda _name: True,
        explicit_category_hint="movies",
    ) == "Movie"
    assert classify_names.classify_video_name(
        name,
        anime_lookup=lambda _name: False,
        explicit_itype_hint="Anime",
    ) == "Anime"


def test_explicit_category_outranks_stale_display_itype() -> None:
    result = classify_names.classify_video_name_result(
        "Show.Name.S01E01.1080p.WEB-DL",
        explicit_category_hint="movies",
        explicit_itype_hint="TV Show",
    )

    assert (result.category, result.itype, result.method) == ("movies", "Movie", "Explicit category")


def test_explicit_movie_category_outranks_stale_tv_itype_for_path_resolution(tmp_path) -> None:
    release = tmp_path / "Movie.Collection.2024.1080p.BluRay"
    release.mkdir()
    _touch(release / "Movie.Collection.2024.1080p.BluRay.mkv", b"x")

    result = classify_explicit.resolve_explicit_path(
        release,
        category_hint="movies",
        itype_hint="TV Show",
        respect_explicit_hint=True,
        anime_lookup=lambda _name: False,
    )

    assert (result.category, result.itype) == ("movies", "Movie")


def test_structured_video_classification_keeps_unknowns_explicit() -> None:
    result = classify_names.classify_video_name_result(
        "Plain.Release.Name.1080p.BluRay",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "misc"
    assert result.itype == "Misc"
    assert result.confidence == "unknown"
    assert result.method == "No reliable signal"
    assert result.evidence == ()
    assert classify_names.classify_video_name("Plain.Release.Name.1080p.BluRay") == "Misc"
    assert classify_names.classify_video_name(
        "Plain.Release.Name.1080p.BluRay",
        assume_movie_if_unknown=True,
    ) == "Movie"


def test_guessit_episode_metadata_requires_an_episode_shaped_name(monkeypatch) -> None:
    monkeypatch.setattr(
        classify_names,
        "parse_release_name",
        lambda _name: {
            "media_type": "tv",
            "season_number": 1,
            "episode_number": 1,
        },
    )

    result = classify_names.classify_video_name_result(
        "Show.Name.S1.1.1080p.WEB-DL",
        anime_lookup=lambda _name: False,
    )
    unshaped = classify_names.classify_video_name_result(
        "Plain.Release.Name.1080p.WEB-DL",
        anime_lookup=lambda _name: False,
    )

    assert (result.category, result.itype, result.method, result.evidence) == (
        "tv",
        "TV Show",
        "guessit",
        ("episode metadata",),
    )
    assert unshaped.category == "misc"


def test_guessit_movie_metadata_requires_year_or_collection(monkeypatch) -> None:
    monkeypatch.setattr(
        classify_names,
        "parse_release_name",
        lambda _name: {
            "media_type": "movie",
            "season_number": None,
            "episode_number": None,
        },
    )

    collection = classify_names.classify_video_name_result(
        "Example.Trilogy.1080p.BluRay",
        anime_lookup=lambda _name: False,
    )
    unknown = classify_names.classify_video_name_result(
        "Example.Release.1080p.BluRay",
        anime_lookup=lambda _name: False,
    )

    assert collection.category == "movies"
    assert collection.method == "guessit"
    assert collection.evidence == ("movie collection",)
    assert unknown.category == "misc"


def test_release_parser_failure_degrades_to_unknown(monkeypatch) -> None:
    def fail_parse(_name):
        raise ValueError("bad release")

    monkeypatch.setattr(classify_names, "parse_release_name", fail_parse)

    result = classify_names.classify_video_name_result(
        "Plain.Release.Name.1080p.BluRay",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "misc"
    assert result.confidence == "unknown"


def test_explicit_path_user_category_overrides_cached_anime_verdict(tmp_path) -> None:
    release = _touch(tmp_path / "Ghost.in.the.Shell.2017.1080p.BluRay.mkv", b"x")

    result = classify_explicit.resolve_explicit_path(
        release,
        category_hint="movies",
        respect_explicit_hint=True,
        anime_lookup=lambda _name: True,
    )

    assert result.category == "movies"
    assert result.itype == "Movie"
    assert result.queue_paths == (release,)


def test_part_numbered_miniseries_are_tv_before_movie_year() -> None:

    releases = [
        "Chernobyl.Part.1.2019.1080p.AMZN.WEB-DL-GROUP.mkv",
        "Band.of.Brothers.Part.3.Carentan.2001.720p.BluRay.x264-GROUP.mkv",
    ]

    for name in releases:
        assert classify_names.classify_video_name(name) == "TV Show", name
        assert classify_names.classify_video_name(name, anime_lookup=anime_cached_lookup) == "TV Show", name


@pytest.mark.parametrize(
    "name",
    [
        "The.Hunger.Games.Mockingjay.Part.1.2014.1080p.BluRay.mkv",
        "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.1080p.BluRay.mkv",
    ],
)
def test_part_numbered_feature_films_remain_movies(name) -> None:
    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "Movie"


def test_part_numbered_web_episode_without_year_is_tv() -> None:
    name = "Series.Name.Part.2.1080p.WEB-DL"
    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "TV Show"


@pytest.mark.parametrize(
    "name",
    [
        "[SubGroup].Attack.on.Titan.-.137.[1080p].[HEVC].mkv",
        "[SubGroup].One.Piece.-.1085.[1080p].mkv",
        "One.Piece.1085.1080p.WEB.h264-GROUP.mkv",
    ],
)
def test_high_absolute_episode_numbers_are_tv(monkeypatch, tmp_path, name) -> None:
    from logic.classify import anime as anime_cache

    entry = _touch(tmp_path / name, b"x")
    monkeypatch.setattr(anime_cache, "get_cached", lambda _name: False)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "TV Show"
    assert classify_content.detect_auto_itype(entry, anime_lookup=lambda _name: False) == "TV Episode"
    assert classify_content.detect_auto_category(entry, anime_lookup=lambda _name: False) == "tv"
    assert classify_content.detect_external_category(name, entry) == "tv"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("The.Matrix.1999.1080p.BluRay.mkv", "Movie"),
        ("Movie.2024.1080p.WEB.mkv", "Movie"),
        ("1917.2019.1080p.mkv", "Movie"),
        ("1408.1080p.BluRay.mkv", "Misc"),
        ("Movie.1440p.WEB.mkv", "Misc"),
    ],
)
def test_high_absolute_episode_pattern_excludes_years_and_resolutions(name, expected) -> None:
    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == expected


@pytest.mark.parametrize(
    "name",
    [
        "UFC.300.PPV.720p.WEB.h264-GROUP.mkv",
        "UFC.300.Main.Event.1080p.WEB.h264.mkv",
        "NFL.2024.Week.5.Cowboys.vs.Eagles.720p.WEB.h264-GROUP.mkv",
    ],
)
def test_recognized_sports_events_are_tv(monkeypatch, tmp_path, name) -> None:
    from logic.classify import anime as anime_cache

    entry = _touch(tmp_path / name, b"x")
    monkeypatch.setattr(anime_cache, "get_cached", lambda _name: False)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "TV Show"
    assert classify_content.detect_auto_itype(entry, anime_lookup=lambda _name: False) == "TV Episode"
    assert classify_content.detect_auto_category(entry, anime_lookup=lambda _name: False) == "tv"
    assert classify_content.detect_external_category(name, entry) == "tv"


@pytest.mark.parametrize(
    "name",
    [
        "Movie.Title.vs.Other.2024.1080p.BluRay.mkv",
        "One.vs.Two.2024.1080p.BluRay.mkv",
        "PPV.Documentary.2023.1080p.WEB.mkv",
        "300.2006.1080p.BluRay.mkv",
    ],
)
def test_sports_event_signal_requires_both_league_and_context(name) -> None:
    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "Movie"


def test_complete_miniseries_phrase_is_tv_before_movie_year() -> None:
    name = "Band.of.Brothers.2001.COMPLETE.MINISERIES.1080p.BluRay"

    assert classify_names.classify_video_name(name, anime_lookup=lambda _name: False) == "TV Show"


@pytest.mark.parametrize(
    ("name", "expected_category"),
    [
        ("asdkjaslkdjalksdjalksjd.mkv", "misc"),
        ("Random.Video.No.Tags.mkv", "misc"),
        ("Plain.Release.Name.1080p.BluRay.mkv", "misc"),
        ("The.Matrix.1999.1080p.BluRay.mkv", "movies"),
        ("Show.Name.S01E01.1080p.WEB.mkv", "tv"),
        ("Anime.Name.Movie.2020.mkv", "movies"),
        ("Show.Name.NL.Subbed.S01E01.mkv", "tv"),
        ("Dual.Audio.Movie.2020.mkv", "movies"),
    ],
)
def test_snapshot_and_scan_external_categories_agree(
    monkeypatch,
    tmp_path,
    name,
    expected_category,
) -> None:
    from logic.classify import anime as anime_cache

    entry = _touch(tmp_path / name, b"x")
    monkeypatch.setattr(anime_cache, "get_cached", lambda _name: False)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    assert classify_content.detect_external_category(name, entry) == expected_category
    assert classify_content.detect_external_category(name, entry) == expected_category


def test_confirmed_anime_cache_still_overrides_external_tv_shape(monkeypatch, tmp_path) -> None:

    name = "Anime.Name.S01E01.1080p.WEB.mkv"
    entry = _touch(tmp_path / name, b"x")
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: True)

    assert classify_content.detect_external_category(name, entry) == "anime"


@pytest.mark.parametrize(
    "name",
    [
        "Random.Video.No.Tags.mkv",
        "Plain.Release.Name.1080p.BluRay.mkv",
    ],
)
def test_snapshot_metadata_keeps_unknown_video_for_manual_review(monkeypatch, tmp_path, name) -> None:
    from logic.pending import tree as pending_tree

    entry = _touch(tmp_path / name, b"x")
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    metadata = pending_tree._build_detected_item_metadata(entry)

    assert metadata["detected_category"] == "misc"
    assert metadata["detection_method"] == "Folder fallback"
    assert metadata["detection_confidence"] == "unknown"
    assert metadata["detection_evidence"] == []


def test_guessit_episode_signal_reaches_snapshot_and_processing(monkeypatch, tmp_path) -> None:
    from logic import processing
    from logic.pending import tree as pending_tree

    episode = _touch(tmp_path / "Show.Name.S1.1.1080p.WEB-DL.mkv", b"x")
    parsed = {
        "media_type": "tv",
        "season_number": 1,
        "episode_number": 1,
    }
    monkeypatch.setattr(classify_names, "parse_release_name", lambda _name: parsed)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    resolution = classify_explicit.resolve_explicit_path(
        episode,
        anime_lookup=lambda _name: False,
    )
    metadata = pending_tree._build_detected_item_metadata(episode)
    processing_items = processing._collect_targeted_job_items(
        paths=[str(episode)],
        item_hints=[],
        category="mixed",
        conf=SimpleNamespace(),
        runtime_job=None,
        process_tv_episodes=True,
    )

    assert resolution.category == "tv"
    assert resolution.queue_paths == (episode,)
    assert resolution.detection_method == "guessit"
    assert resolution.detection_evidence == ("episode metadata",)
    assert classify_content.detect_content_itype(episode.name, episode, "") == "TV Episode"
    assert classify_content.detect_auto_category(episode, anime_lookup=lambda _name: False) == "tv"
    assert metadata["detected_category"] == "tv"
    assert metadata["detection_method"] == "guessit"
    assert metadata["detection_confidence"] == "strong"
    assert metadata["detection_evidence"] == ["episode metadata"]
    assert processing_items == [(episode, "tv")]


def test_guessit_movie_collection_reaches_explicit_and_snapshot_paths(monkeypatch, tmp_path) -> None:
    from logic.pending import tree as pending_tree

    collection = tmp_path / "Example.Trilogy.1080p.BluRay"
    _touch(collection / "First.Film.2001.1080p.BluRay.mkv", b"a")
    _touch(collection / "Second.Film.2003.1080p.BluRay.mkv", b"b")
    parsed = {
        "media_type": "movie",
        "season_number": None,
        "episode_number": None,
    }
    monkeypatch.setattr(classify_names, "parse_release_name", lambda _name: parsed)
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    resolution = classify_explicit.resolve_explicit_path(
        collection,
        category_hint="external",
        anime_lookup=lambda _name: False,
    )
    metadata = pending_tree._build_detected_item_metadata(collection, category_hint="external")

    assert resolution.category == "movies"
    assert resolution.queue_paths == (collection,)
    assert resolution.detection_method == "guessit"
    assert resolution.detection_evidence == ("movie collection",)
    assert classify_content.detect_content_itype(collection.name, collection, "") == "Movie"
    assert metadata["detected_category"] == "movies"
    assert metadata["detection_method"] == "guessit"
    assert metadata["detection_evidence"] == ["movie collection"]


def test_non_video_extension_episode_is_reported_as_ignored(tmp_path) -> None:
    episode = _touch(tmp_path / "Show.Name.S01E01.WEB")

    result = classify_explicit.resolve_explicit_path(
        episode,
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Episode"
    assert result.queue_paths == ()
    assert _ignored_path_reasons(result) == [
        (episode, "No recognized video extension"),
    ]


def test_complete_series_container_queues_valid_episode_leaves(tmp_path) -> None:
    complete = tmp_path / "Show.Name.Complete.Series.1080p.WEB-DL"
    s01e01 = _touch(complete / "Season 1" / "Show.Name.S01E01.1080p.WEB-DL.mkv", b"a")
    s02e01 = _touch(complete / "Season 2" / "Show.Name.S02E01.1080p.WEB-DL.mkv", b"b")
    extra = _touch(complete / "Season 2" / "readme.txt", "x")

    result = classify_explicit.resolve_explicit_path(
        complete,
        category_hint="tv",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Show"
    assert result.queue_paths == (s01e01, s02e01)
    assert _ignored_path_reasons(result) == [
        (extra, "TV season pack extra/non-video content"),
    ]


def test_single_nested_movie_filename_classifies_neutral_parent(tmp_path) -> None:
    parent = tmp_path / "TV Shows"
    _touch(parent / "Movie.Name.2020.1080p.BluRay.mkv", b"x")

    result = classify_explicit.resolve_explicit_path(
        parent,
        anime_lookup=lambda _name: False,
    )

    assert result.category == "movies"
    assert result.itype == "Movie"
    assert result.queue_paths == (parent,)


def test_one_year_bearing_child_does_not_classify_multi_video_parent_as_movie(tmp_path) -> None:
    parent = tmp_path / "Mixed Videos"
    _touch(parent / "Movie.Name.2020.1080p.BluRay.mkv", b"x")
    _touch(parent / "Untitled.Release.1080p.WEB.mkv", b"x")

    result = classify_explicit.resolve_explicit_path(
        parent,
        anime_lookup=lambda _name: False,
    )

    assert result.category != "movies"


def test_nested_season_and_episode_folders_supply_tv_context(tmp_path) -> None:
    show = tmp_path / "Show"
    episode = _touch(show / "Season 1" / "Episode 1" / "file.mkv", b"x")

    result = classify_explicit.resolve_explicit_path(
        show,
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Show"
    assert result.queue_paths == ()
    assert _ignored_path_reasons(result) == [
        (episode, "TV episode missing media source token"),
    ]


def test_resolve_explicit_path_uses_jikan_for_anime_and_filters_extras(tmp_path) -> None:
    anime_dir = tmp_path / "Movies" / "Frieren"
    episode_one = _touch(anime_dir / "[SubsPlease] Frieren - 01 (1080p).mkv", b"a")
    episode_two = _touch(anime_dir / "[SubsPlease] Frieren - 02 (1080p).mkv", b"b")
    opening = _touch(anime_dir / "[SubsPlease] Frieren - NCOP (1080p).mkv", b"c")

    result = classify_explicit.resolve_explicit_path(
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

    result = classify_explicit.resolve_explicit_path(
        season,
        category_hint="tv",
        anime_lookup=lambda _name: False,
    )

    assert result.category == "tv"
    assert result.itype == "TV Show"
    assert result.detection_method == "Episode pattern"
    assert result.queue_paths == (episode_one, episode_two)
    # queue-backend-11: OP/ED/OVA/preview/special files are pack extras.
    assert _ignored_path_reasons(result) == [
        (opening, "TV season pack extra/sample"),
        (notes, "TV season pack extra/non-video content"),
    ]

def test_resolve_explicit_path_disc_content_is_flagged_and_ignored(tmp_path) -> None:
    release_dir = tmp_path / "Movie.Name.2024.COMPLETE.BluRay"
    stream = _touch(release_dir / "BDMV" / "00001.m2ts", b"a")
    index = _touch(release_dir / "BDMV" / "index.bdmv", b"b")

    result = classify_explicit.resolve_explicit_path(release_dir, anime_lookup=lambda _name: False)

    assert result.category == "movies"
    assert result.itype == "Movie"
    assert result.detection_method == "Disc scan"
    assert result.queue_paths == ()
    assert result.content_flags == ("disc",)
    assert _ignored_path_reasons(result) == [
        (stream, "Disc content"),
        (index, "Disc content"),
    ]


@pytest.mark.parametrize("extension", [".iso", ".img", ".mdf", ".mds", ".nrg"])
def test_bare_disc_images_are_apps_not_video_discs(monkeypatch, tmp_path, extension) -> None:
    from logic.pending import tree as pending_tree

    release_dir = tmp_path / "SomeGame-RUNE"
    image = _touch(release_dir / f"somegame{extension}", b"a")
    monkeypatch.setattr("logic.classify.anime.get_cached", lambda _name: False)

    assert classify_content.detect_auto_itype(release_dir, anime_lookup=lambda _name: False) == "App"
    assert classify_content.detect_auto_category(release_dir, anime_lookup=lambda _name: False) == "apps"
    assert classify_content.detect_external_category(release_dir.name, release_dir) == "apps"

    result = classify_explicit.resolve_explicit_path(
        release_dir,
        anime_lookup=lambda _name: False,
    )

    assert result.category == "apps"
    assert result.itype == "App"
    assert result.detection_method == "File scan"
    assert result.queue_paths == (image,)
    assert result.content_flags == ()
    assert pending_tree._build_detected_item_metadata(release_dir)["detected_category"] == "apps"


def test_installer_folder_with_disc_image_is_app_content(tmp_path) -> None:
    release_dir = tmp_path / "BigGame.Repack-FitGirl"
    image = _touch(release_dir / "game.iso", b"a")
    installer = _touch(release_dir / "setup.exe", b"b")
    data = _touch(release_dir / "setup-1.bin", b"c")

    assert classify_content.detect_auto_category(release_dir, anime_lookup=lambda _name: False) == "apps"
    result = classify_explicit.resolve_explicit_path(release_dir, anime_lookup=lambda _name: False)

    assert result.category == "apps"
    assert result.itype == "App"
    assert result.queue_paths == (image, installer)
    assert _ignored_path_reasons(result) == [(data, "Non-app folder content")]


def test_disc_words_without_structure_do_not_force_movie(tmp_path) -> None:

    release_dir = tmp_path / "Game.Full.BluRay"
    _touch(release_dir / "installer.iso", b"a")
    marker_only_file = _touch(tmp_path / "VIDEO_TS" / "readme.txt", b"b")

    assert classify_content.detect_auto_category(release_dir, anime_lookup=lambda _name: False) == "apps"
    assert classify_content.detect_auto_itype(release_dir, anime_lookup=lambda _name: False) == "App"
    assert classify_content.classify_standalone_file_category(marker_only_file) == ""


def test_resolve_explicit_path_tv_disc_pack_stays_tv_when_anime_not_confirmed(tmp_path) -> None:
    season_dir = tmp_path / "Season 1"
    _touch(season_dir / "DISC_1" / "VIDEO_TS" / "VIDEO_TS.IFO", b"a")

    result = classify_explicit.resolve_explicit_path(
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

    result = classify_explicit.resolve_explicit_path(
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

    result = classify_explicit.resolve_explicit_path(books_dir)

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

        items = pending_roots.scan_folder_items(root, "external")

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
            assert started[0][0] == pending_api._background_anime_check, case_name
            assert started[0][1] == (snapshot,), case_name
        else:
            assert started == [], case_name

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

def test_scan_pending_snapshot_lazy_tree_sizes_each_top_level_folder_once(tmp_path, monkeypatch) -> None:
    # queue-backend-16 (DECISIONS: lazy one-level pending tree): top-level folders are
    # scanned without children, so each is sized once with compute_size_uncached.
    from core.utils import compute_size_uncached as real_compute_size
    from logic.pending import tree as pending_tree

    external_dir = tmp_path / "external"
    release_dir = external_dir / "Release.Dir"
    nested_dir = release_dir / "CD1"
    nested_dir.mkdir(parents=True)
    (nested_dir / "part1.mkv").write_bytes(b"a" * 3)
    (release_dir / "sample.nfo").write_text("nfo", encoding="utf-8")

    conf = SimpleNamespace(folder_paths=[{"path": str(external_dir), "category": "external"}], skip_files=None)

    sized: list[Path] = []

    def counting_size(path: Path) -> int:
        sized.append(path)
        return real_compute_size(path)

    _configure_pending_snapshot_environment(
        monkeypatch,
        conf,
        dashboard_data=({}, {}, {}),
        configured_folders=[("external", external_dir)],
        compute_size_uncached=counting_size,
    )

    payload = pending_tree.scan_pending_snapshot()

    item = payload["items"]["external"][0]["items"][0]
    assert sized == [release_dir]
    assert item["size"] == 6
    assert item["child_count"] == 2
    assert item["children"] == []
    assert _pending_lazy_children(payload, item)[0]["size"] == 3
    assert payload["summary"]["external"] == 1

def test_scan_pending_snapshot_exposes_bulk_selection_policy(tmp_path, monkeypatch) -> None:
    from logic.pending import tree as pending_tree

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
        conf,
        dashboard_data=(set(), {}, {}),
        configured_folders=[("external", external_dir)],
    )

    payload = pending_tree.scan_pending_snapshot()

    assert payload["items"]["external"][0]["allow_bulk_selection"] is False

def test_scan_pending_snapshot_external_dir_completion_rolls_up_from_deferred_children(tmp_path, monkeypatch) -> None:
    # queue-backend-16 (deferred completion as on the server): a folder is done for an
    # indexer when all of its direct children are in the upload map.
    from logic.pending import tree as pending_tree

    external_dir = tmp_path / "external"
    release_dir = external_dir / "Movie.Name.2026"
    release_dir.mkdir(parents=True)
    movie_file = release_dir / "Movie.Name.2026.1080p.WEB-DL.mkv"
    movie_file.write_bytes(b"x" * 10)

    conf = SimpleNamespace(folder_paths=[{"path": str(external_dir), "category": "external"}], skip_files=None)
    indexer = SimpleNamespace(id="idx1", name="Indexer 1", color="#fff", icon="database", favicon_url=None)

    _configure_pending_snapshot_environment(
        monkeypatch,
        conf,
        dashboard_data=(set(), {movie_file.name: {"idx1"}}, {}),
        configured_folders=[("external", external_dir)],
        indexers=[indexer],
        resolve_backfill=lambda _idx, _conf: True,
    )

    payload = pending_tree.scan_pending_snapshot()

    item = payload["items"]["external"][0]["items"][0]
    assert item["is_dir"] is True
    assert item["auto_selectable"] is True
    assert item["completed"] is True
    assert item["indexers"] == {"idx1": True}
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
    monkeypatch.setattr(autoupload, "detect_auto_category", lambda _path: "tv")

    autoupload._trigger_uploads(str(monitored_dir), {release_dir.name: "movies"})

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
                "detection_method": "Episode pattern",
            },
            "expected_child_name": None,
        },
        {
            "name": "unknown-external-category-is-misc",
            "folder_category": "external",
            "root_name": "external-unknown",
            "location": "external",
            "files": {"Untyped.Release/readme.txt": "hello"},
            "anime_match_name": None,
            "expected": {"itype": "Misc", "detected_category": "misc"},
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
    assert top_item["auto_selectable"] is True
    # queue-backend-18: an episode needs its own source token for individual upload.
    assert child_map[episode.name]["auto_selectable"] is False
    assert child_map[episode.name]["eligible"] is False
    assert child_map[episode.name]["auto_select_reason"].startswith("Missing quality source in filename")
    assert child_map[extra.name]["auto_select_ignored"] is True
    assert child_map[extra.name]["auto_select_reason"] == "No episode pattern"


def test_scan_pending_all_keeps_pokemon_folder_and_episodes_anime(monkeypatch, tmp_path) -> None:
    anime_root = tmp_path / "Anime"
    show_dir = anime_root / "0-Pokemon Horizon - Singles"
    names = [
        "Pokemon.S20E45.From.So.Far.Away.Part.2.1080p.WEBRip.10bit.EAC3.2.0.x265-iVy.mkv",
        "Pokémon.S20E112.Mega.Evolution.Roy.1080p.WEBRip.10bit.EAC3.2.0.x265-iVy.mkv",
    ]
    for name in names:
        _touch(show_dir / name)

    result = _configure_pending_scan_all(
        monkeypatch,
        "anime",
        anime_root,
        anime_cache_lookup=lambda _name: True,
    )

    top_item = _get_pending_top_item(result, "anime")
    assert top_item["detected_category"] == "anime"
    assert top_item["itype"] == "Anime"
    assert top_item["auto_selectable"] is True
    assert "pokemon" in {name.casefold() for name in pending_index.collect_anime_check_names(result)}


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
    assert child_map[extra.name]["completed"] is False
    assert child_map[extra.name]["indexers"]["idx1"] is False
    assert child_map[note.name]["auto_select_ignored"] is True
    assert child_map[note.name]["completed"] is False
    assert child_map[note.name]["indexers"]["idx1"] is False
    assert top_item["completed"] is True
    assert top_item["indexers"]["idx1"] is True

def test_resolve_submission_category_cases(tmp_path) -> None:
    import logic.processing as processing

    cases = [
        ("rejects-ambiguous-video-misc", "Untitled.Release.mkv", "misc", "Misc", None, "cannot be submitted as Misc"),
        ("preserves-explicit-tv", "Show.Name.S00E01.1080p.WEB-DL.mkv", "tv", "Misc", "tv", None),
        ("preserves-explicit-anime", "Anime.Name.S01E01.1080p.WEB-DL.mkv", "anime", "TV Episode", "anime", None),
        (
            "preserves-explicit-audiobook",
            "Author.Name.Novel.Unabridged.m4b",
            "audiobooks",
            "Audiobook",
            "audiobooks",
            None,
        ),
        (
            "accepts-chaptered-mp3-audiobook",
            "Author.Name.Novel.Chapter.01.mp3",
            "audiobooks",
            "Audiobook",
            "audiobooks",
            None,
        ),
        (
            "rejects-audiobook-without-audio",
            "Author.Name.Novel.txt",
            "audiobooks",
            "Audiobook",
            None,
            "audiobook category requires audio file types",
        ),
        ("disc-anime-submits-as-anime", "Anime.Name.S01E01.1080p.BluRay.mkv", "disc", "Anime", "anime", None),
        ("disc-tv-submits-as-tv", "Show.Name.S01E01.1080p.BluRay.mkv", "disc", "TV Episode", "tv", None),
        ("disc-movie-submits-as-movies", "Movie.Name.2026.1080p.BluRay.mkv", "disc", "Movie", "movies", None),
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


def test_disc_selection_queues_the_whole_release_and_routes_by_underlying_type(monkeypatch, tmp_path) -> None:
    import logic.processing as processing

    release = tmp_path / "Show.Name.S01.DVD"
    _touch(release / "VIDEO_TS" / "VIDEO_TS.IFO")
    monkeypatch.setattr("logic.classify.explicit.cached_lookup", lambda _name: True)

    items = processing._collect_targeted_job_items(
        paths=[str(release)],
        item_hints=[{"path": str(release), "category": "disc", "itype": "Disc"}],
        category="mixed",
        conf=SimpleNamespace(),
        runtime_job=None,
        process_tv_episodes=True,
    )

    assert items == [(release, "disc")]
    assert processing._processing_db_type(release, "disc") == "DISC"
    assert processing._resolve_submission_category(release, "disc", "DISC") == "tv"


def test_targeted_job_assembly_never_uses_live_anime_lookup(monkeypatch, tmp_path) -> None:
    from logic.classify import anime as anime_cache
    import logic.processing as processing

    movie = _touch(tmp_path / "Movie.Name.2024.1080p.BluRay.mkv", b"x")

    def fail_live_lookup(_name):
        raise AssertionError("targeted job assembly called the live anime detector")

    monkeypatch.setattr(anime_cache, "is_anime", fail_live_lookup)
    monkeypatch.setattr("logic.classify.explicit.cached_lookup", lambda _name: False)

    items = processing._collect_targeted_job_items(
        paths=[str(movie)],
        item_hints=[{"path": str(movie), "category": "movies", "itype": "Movie"}],
        category="mixed",
        conf=SimpleNamespace(),
        runtime_job=None,
        process_tv_episodes=True,
    )

    assert items == [(movie, "movies")]


def test_dashboard_snapshot_rows_skip_external_items_without_category() -> None:

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

    rows = pending_view.iter_dashboard_snapshot_rows(snapshot)

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
    assert top_item["itype"] == "Disc"
    assert top_item["detected_category"] == "disc"
    assert top_item["detection_method"] == "Disc scan"
    assert top_item["detection_flags"] == ["disc"]
    assert vob_item["detected_category"] == "disc"
    assert vob_item["auto_select_ignored"] is False
    assert vob_item["auto_selectable"] is True
    assert vob_item["completed"] is False
    assert top_item["completed"] is False

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
    # queue-backend-19 (DECISIONS: only TV/anime keep ignore flags).
    assert child_map[cover.name]["auto_select_ignored"] is False
    assert child_map[cover.name]["detected_category"] == "books"
    assert child_map[cover.name]["completed"] is False


def test_video_folder_is_not_reclassified_by_book_or_audio_sidecars(tmp_path) -> None:

    release = tmp_path / "Pokemon.S20"
    _touch(release / "Pokemon.S20E01.1080p.WEBRip.mkv")
    _touch(release / "episode-guide.epub")
    _touch(release / "commentary.mp3")

    assert classify_content._directory_extension_first_category(release) == ""


def test_audio_dominant_album_ignores_one_promo_video(tmp_path) -> None:
    release = tmp_path / "Artist - Album Name2 (2020) [FLAC]"
    tracks = tuple(_touch(release / f"{number:02d}.flac", b"x") for number in range(1, 4))
    cover = _touch(release / "cover.jpg", b"x")
    promo = _touch(release / "promo.mp4", b"x")

    assert classify_content.detect_auto_itype(release, anime_lookup=lambda _name: False) == "Music"
    assert classify_content.detect_auto_category(release, anime_lookup=lambda _name: False) == "music"
    assert classify_content.detect_external_category(release.name, release) == "music"

    result = classify_explicit.resolve_explicit_path(release, anime_lookup=lambda _name: False)

    assert result.category == "music"
    assert result.itype == "Music"
    assert result.queue_paths == tracks
    assert _ignored_path_reasons(result) == [
        (cover, "Non-music folder content"),
        (promo, "Non-music folder content"),
    ]


def test_audio_video_tie_remains_movie_led(tmp_path) -> None:
    release = tmp_path / "Movie.Name.2024.1080p.BluRay"
    _touch(release / "Movie.Name.2024.1080p.BluRay.mkv", b"x")
    _touch(release / "soundtrack.flac", b"x")

    assert classify_content.detect_auto_itype(release, anime_lookup=lambda _name: False) == "Movie"
    assert classify_content.detect_auto_category(release, anime_lookup=lambda _name: False) == "movies"
    assert classify_content.detect_external_category(release.name, release) == "movies"


def test_track_numbered_mp3_folder_remains_unresolved_without_book_evidence(tmp_path) -> None:

    release = tmp_path / "Brandon Sanderson - The Way of Kings"
    for number in range(1, 6):
        _touch(release / f"Track {number:02d}.mp3", b"x")

    assert classify_content.detect_auto_itype(release, anime_lookup=lambda _name: False) == "Misc"
    assert classify_content.detect_auto_category(release, anime_lookup=lambda _name: False) == "misc"
    assert classify_content._directory_extension_first_category(release) == ""


@pytest.mark.parametrize(
    ("folder_name", "file_pattern"),
    [
        ("Author - Novel", "Chapter {number:02d}.mp3"),
        ("Author - Novel (Unabridged)", "Track {number:02d}.mp3"),
        ("Author - Novel - Narrated by Reader", "Track {number:02d}.mp3"),
    ],
)
def test_mp3_audiobook_evidence_classifies_as_audiobook(
    tmp_path,
    folder_name,
    file_pattern,
) -> None:

    release = tmp_path / folder_name
    for number in range(1, 4):
        _touch(release / file_pattern.format(number=number), b"x")

    assert classify_content.detect_auto_itype(release, anime_lookup=lambda _name: False) == "Audiobook"
    assert classify_content.detect_auto_category(release, anime_lookup=lambda _name: False) == "audiobooks"
    assert classify_content._directory_extension_first_category(release) == "audiobooks"


def test_album_hint_keeps_track_numbered_mp3_folder_as_music(tmp_path) -> None:
    release = tmp_path / "Artist - Album Name"
    for number in range(1, 4):
        _touch(release / f"Track {number:02d}.mp3", b"x")

    assert classify_content.detect_auto_itype(release, anime_lookup=lambda _name: False) == "Music"
    assert classify_content.detect_auto_category(release, anime_lookup=lambda _name: False) == "music"


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

    categories = pending_roots.get_configured_category_folders(_Conf(), include_external=False, must_exist=True)
    assert categories == [("movies", movies)]

    all_categories = pending_roots.get_configured_category_folders(_Conf(), include_external=True, must_exist=False)
    assert all_categories == [
        ("movies", movies),
        ("external", ext),
        ("external", implicit_ext),
        ("tv", tmp_path / "missing-tv"),
    ]

def test_bulk_selection_parity_between_ui_stamps_and_server_filter(tmp_path, monkeypatch) -> None:
    # W10-B9: the rows the tree stamps as bulk-selectable are exactly the rows the server accepts.
    from logic.pending import tree as pending_tree

    manual_root = tmp_path / "manual"
    bulk_root = tmp_path / "bulk"
    for root in (manual_root, bulk_root):
        release = root / "Movie.2026.1080p.WEB-DL"
        release.mkdir(parents=True)
        (release / "Movie.2026.1080p.WEB-DL.mkv").write_bytes(b"x")
        (root / "Other.Movie.2025.1080p.BluRay.mkv").write_bytes(b"x")
    conf = SimpleNamespace(
        folder_paths=[
            {"path": str(manual_root), "category": "external", "allow_bulk_selection": False},
            {"path": str(bulk_root), "category": "external", "allow_bulk_selection": True},
        ],
        skip_files=None,
    )
    _configure_pending_snapshot_environment(
        monkeypatch,
        conf,
        dashboard_data=(set(), {}, {}),
        configured_folders=[("external", manual_root), ("external", bulk_root)],
    )

    payload = pending_tree.scan_pending_snapshot()

    rows = [
        (group, item)
        for group in payload["items"]["external"]
        for item in group.get("items") or []
    ]
    assert len(rows) == 4
    stamped = {
        item["path"]
        for group, item in rows
        if group.get("allow_bulk_selection", True) and item.get("auto_selectable")
    }
    accepted, excluded = deps_api._filter_bulk_selectable_items(
        [{"path": item["path"]} for _group, item in rows if item.get("auto_selectable")], conf
    )
    assert stamped == {entry["path"] for entry in accepted}
    assert stamped and excluded == 2


def test_pending_rows_and_children_carry_the_server_upload_itype(tmp_path, monkeypatch) -> None:
    from logic.pending import tree as pending_tree

    external_dir = tmp_path / "external"
    pack = external_dir / "Show.S01.1080p.WEB-DL"
    pack.mkdir(parents=True)
    for episode in (1, 2):
        (pack / f"Show.S01E0{episode}.1080p.WEB-DL.mkv").write_bytes(b"x")
    conf = SimpleNamespace(folder_paths=[{"path": str(external_dir), "category": "external"}], skip_files=None)
    _configure_pending_snapshot_environment(
        monkeypatch,
        conf,
        dashboard_data=(set(), {}, {}),
        configured_folders=[("external", external_dir)],
    )

    payload = pending_tree.scan_pending_snapshot()
    slim = pending_api._slim_pending_items(payload["items"])
    top = slim["external"][0]["items"][0]
    assert top["upload_itype"] == top["itype"]

    children = pending_children.build_external_children_for_request(payload, top["key"], top["path"])["children"]
    assert children
    assert {child["upload_itype"] for child in children} == {"TV Episode"}


def test_upload_itype_falls_back_to_the_category_upload_mapping() -> None:
    assert pending_selection.upload_itype({"itype": "External", "detected_category": "tv", "is_dir": True}) == "TV Show"
    assert pending_selection.upload_itype({"itype": "", "category": "books"}) == "Ebook"
    assert pending_selection.upload_itype({"itype": "Anime", "category": "tv"}) == "Anime"
    assert jobs_api._default_itype_for_category(Path("missing.mkv"), "tv") == "TV Episode"


def test_autoupload_characterization_monitors_manual_selection_only_roots(monkeypatch) -> None:
    # W10-B9 characterization: auto-upload does NOT consult "Manual selection only"
    # (allow_bulk_selection=False); a monitored root is watched either way. Kept as-is
    # pending the owner's decision, so it is not routed through pending.selection.
    conf = SimpleNamespace(
        get_folder_path_entries=lambda: [
            {"path": "/media/manual", "category": "movies", "monitor": True, "allow_bulk_selection": False},
            {"path": "/media/bulk", "category": "tv", "monitor": True},
            {"path": "/media/off", "category": "tv", "monitor": False},
        ]
    )
    monkeypatch.setattr("core.config.get_config", lambda: conf)

    assert autoupload._get_monitored_folders() == [
        {"path": "/media/manual", "category": "movies"},
        {"path": "/media/bulk", "category": "tv"},
    ]

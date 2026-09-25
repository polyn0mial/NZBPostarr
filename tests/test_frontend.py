# ruff: noqa: F403,F405

"""NZBPostarr frontend tests."""

from tests.support import *


@pytest.mark.skipif(
    not _playwright_browsers_available(),
    reason="Playwright browsers are not installed",
)
def test_e2e_web_page_loads(base_url: str, page: Page) -> None:
    assert expect is not None
    if not base_url:
        pytest.skip("base_url fixture is not configured")

    page.set_default_timeout(120000)
    page.set_default_navigation_timeout(120000)

    cases = [
        ("/", "/api/dashboard/summary", "Dashboard", "Overview", "heading", "Console Output"),
        ("/queue", "/api/pending/summary", "Queue", "Pending Items", "id", "#upload-queue-section"),
        ("/settings", "/api/settings", "Settings", "Updates", "placeholder", "Search settings..."),
    ]

    for case_name, route, response_path, title, primary_heading, locator_kind, locator_value in (
        ("dashboard", *cases[0]),
        ("queue", *cases[1]),
        ("settings", *cases[2]),
    ):
        with page.expect_response(
            lambda response, response_path=response_path: _path_matches(response.url, response_path)
            and response.status == 200,
            timeout=120000,
        ):
            page.goto(f"{base_url}{route}", wait_until="commit")

        _expect_title(page, title)
        expect(page.get_by_role("heading", name=primary_heading)).to_be_visible()
        expect(_page_smoke_locator(page, locator_kind, locator_value)).to_be_visible()

def test_legacy_queue_and_history_pages_redirect_to_canonical_routes() -> None:
    cases = [
        ("pending", app_mod.redirect_pending_to_queue, "/queue"),
        ("pending.html", app_mod.redirect_pending_to_queue, "/queue"),
        ("uploads", app_mod.redirect_uploads_to_history, "/history"),
        ("uploads.html", app_mod.redirect_uploads_to_history, "/history"),
    ]

    for page_name, handler, destination in cases:
        response = _run_async(handler(_make_request(f"/{page_name}")))
        assert response.status_code == 301, page_name
        assert response.headers["location"] == destination, page_name

def test_legacy_multi_episode_folder_stays_tv_across_detectors(tmp_path) -> None:
    show_dir = tmp_path / "Aliens In The Family [1996] - Hensen"
    _touch(show_dir / "Season 01 - TV" / "Aliens in the Family - 101 - Meet the Brodys (Divx).avi", b"a")
    _touch(show_dir / "Season 01 - TV" / "Aliens in the Family - 102 - Bobut Conquers All (Divx).avi", b"b")

    assert pending_snapshot_mod.detect_content_itype(show_dir.name, show_dir, "") == "TV Show"
    assert pending_snapshot_mod.detect_external_category(show_dir.name, show_dir) == "tv"
    assert pending_scan.detect_auto_itype(show_dir) == "TV Show"
    assert pending_scan.detect_auto_category(show_dir) == "tv"

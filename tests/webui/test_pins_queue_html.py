"""Source-text pins on the queue page template (webui/queue.html and its partials)."""

from tests.webui._source import REPO_ROOT, _read_repo_text


def _queue_template_source() -> str:
    """queue.html plus webui/partials/queue/**, the files the page now includes."""
    partials = sorted((REPO_ROOT / "webui" / "partials" / "queue").rglob("*.html"))
    return "\n".join([_read_repo_text("webui", "queue.html")] + [p.read_text(encoding="utf-8") for p in partials])


def test_webui_queue_html_includes_expected_selection_logic() -> None:
    cases = [
        (
            "queue-html",
            _queue_template_source(),
            [
                '@click.stop="forceUploadExtChild(child, $event)"',
                '@click.stop="forceUploadExtChild(gc, $event)"',
                ':disabled="isPackOnlyExternalChild(child) || !getUploadCategoryForItem(child)"',
                ':disabled="isPackOnlyExternalChild(gc) || !getUploadCategoryForItem(gc)"',
                '@click.stop="correctAnimeCache(item, getCategoryForItem(item) !== \'anime\')"',
                '@click.stop="correctAnimeCache(child, getCategoryForItem(child) !== \'anime\')"',
                '@click.stop="correctAnimeCache(gc, getCategoryForItem(gc) !== \'anime\')"',
            ],
        ),
    ]

    for case_name, text, required_substrings in cases:
        for needle in required_substrings:
            assert needle in text, f"{case_name}: {needle}"


def test_webui_queue_html_performance_guards_are_present() -> None:
    queue_html = _queue_template_source()

    assert "pending-virtual-row" in queue_html
    assert "Completed Job Items Modal" in queue_html


def test_queue_job_count_is_below_progress_and_describes_remaining_items() -> None:
    queue_html = _queue_template_source()

    progress_bar = queue_html.index('class="h-1.5 bg-notion-bg-secondary')
    remaining_count = queue_html.index("{{ jobItemCount(job) }}", progress_bar)
    assert remaining_count > progress_bar
    # Consolidation decision: the active job row shows what is uploading now (server layout).
    assert "{{ isJobQueueActiveEntry(job) ? jobTitle(job) : jobDisplayName(job) }}" in queue_html
    assert "Release the whole-queue hold" not in queue_html


def test_pending_rows_restore_compact_category_and_status_badges() -> None:
    queue_html = _queue_template_source()

    # Consolidation decision: the server's status capsules sit before an 88px capsule select.
    assert queue_html.count('title="Completed">') >= 4
    assert queue_html.count(">Done</span>") == 0
    assert queue_html.count("h-[1.05rem] px-1.5 rounded-full border border-notion-border/70 text-[8px]") == 3

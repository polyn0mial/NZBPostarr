"""Source-text pins on the queue page template (webui/queue.html)."""

from tests.webui._source import _read_repo_text


def test_webui_queue_html_includes_expected_selection_logic() -> None:
    cases = [
        (
            "queue-html",
            ("webui", "queue.html"),
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

    for case_name, path_parts, required_substrings in cases:
        text = _read_repo_text(*path_parts)
        for needle in required_substrings:
            assert needle in text, f"{case_name}: {needle}"


def test_webui_queue_html_performance_guards_are_present() -> None:
    queue_html = _read_repo_text("webui", "queue.html")

    assert "pending-virtual-row" in queue_html
    assert "Completed Job Items Modal" in queue_html


def test_queue_job_count_is_below_progress_and_describes_remaining_items() -> None:
    queue_html = _read_repo_text("webui", "queue.html")

    progress_bar = queue_html.index('class="h-1.5 bg-notion-bg-secondary')
    remaining_count = queue_html.index("{{ jobItemCount(job) }}", progress_bar)
    assert remaining_count > progress_bar
    # Consolidation decision: the active job row shows what is uploading now (server layout).
    assert "{{ isJobQueueActiveEntry(job) ? jobTitle(job) : jobDisplayName(job) }}" in queue_html
    assert "Release the whole-queue hold" not in queue_html


def test_pending_rows_restore_compact_category_and_status_badges() -> None:
    queue_html = _read_repo_text("webui", "queue.html")

    # Consolidation decision: the server's status capsules sit before an 88px capsule select.
    assert queue_html.count('title="Completed">') >= 4
    assert queue_html.count(">Done</span>") == 0
    assert queue_html.count("h-[1.05rem] px-1.5 rounded-full border border-notion-border/70 text-[8px]") == 3

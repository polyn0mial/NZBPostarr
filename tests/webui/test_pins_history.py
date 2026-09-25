"""Source-text pins on the history page script and template."""

from tests.webui._source import _read_repo_text


def test_history_known_issues_panel_wires_list_and_mute_action() -> None:
    """The Known Issues panel (grouped failed-submission signatures) must fetch
    from the grouped-errors endpoint and post to the mute/unmute endpoints -
    removing the wiring would leave the panel rendering a permanently empty or
    inert list."""
    history_js = _read_repo_text("webui", "assets", "js", "pages", "history.js")
    history_html = _read_repo_text("webui", "history.html")

    for needle in (
        "async loadKnownIssues()",
        "await this.apiFetch(`/api/uploads/errors/grouped?${params}`)",
        "async toggleIssueMute(issue)",
        "'/api/uploads/errors/unmute'",
        "'/api/uploads/errors/mute'",
        "await this.apiPost(endpoint, { indexer_id: issue.indexer_id, signature: issue.signature })",
    ):
        assert needle in history_js, needle

    for needle in (
        "Known Issues",
        '@click.stop="loadKnownIssues"',
        '@click="toggleIssueMute(issue)"',
        "issue.muted ? 'Unmute' : 'Mute'",
    ):
        assert needle in history_html, needle

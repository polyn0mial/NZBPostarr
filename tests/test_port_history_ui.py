"""History page fixes ported from the live server (area history-ui)."""

from tests.conftest import _read_repo_text


def _history_html() -> str:
    return _read_repo_text("webui", "history.html")


def _history_js() -> str:
    # The page script is split into modules under pages/history/ (index.js + helpers).
    return "\n".join(
        _read_repo_text("webui", "assets", "js", "pages", "history", name)
        for name in ("index.js", "groups.js", "known-issues.js", "selection.js")
    )


def test_history_destination_tooltips_use_dest_infotip_card() -> None:
    """Flat and grouped item-row destination dots show the server's tooltip card: a centred
    favicon + name title row and a 'Successful Upload' / red 'Failed: ...' body line."""
    html = _history_html()

    assert html.count('<div class="infotip-content dest-infotip">') == 2
    assert html.count('<div class="dest-infotip-title">') == 2
    assert html.count("<div v-if=\"dest.status === 'failed'\" class=\"dest-infotip-body is-failed\">Failed: {{ dest.error || 'Unknown Error' }}</div>") == 2
    assert html.count('<div v-else class="dest-infotip-body">Successful Upload</div>') == 2
    assert 'style="color: var(--notion-error)"' not in html


def test_history_tooltip_styles_are_scoped_to_the_uploads_table() -> None:
    """The wider tooltip only applies on the History table (page-local style block), so
    tooltips on other pages keep the shared size."""
    html = _history_html()
    head, _, body = html.partition("{% block content %}")

    assert "{% block extra_head %}" in head
    for needle in (
        ".uploads-table .infotip-content {",
        "min-width: 140px;",
        "max-width: min(320px, calc(100vw - 32px));",
        ".uploads-table .dest-infotip {",
        "max-width: min(280px, calc(100vw - 32px));",
        ".dest-infotip-title {",
        ".dest-infotip-body {",
        ".dest-infotip-body.is-failed {",
    ):
        assert needle in head, needle
    assert "uploads-table" in body


def test_history_top_rows_open_destination_tooltips_downward() -> None:
    """The first two rows (flat and grouped: group, season and item rows) open destination
    tooltips below the dot so the table header does not clip them."""
    html = _history_html()

    assert 'v-for="(item, itemIndex) in (uploads || [])"' in html
    assert 'v-for="(row, rowIndex) in groupedRows"' in html
    assert html.count(":class=\"itemIndex < 2 ? 'infotip-bottom' : ''\"") == 1
    assert html.count(":class=\"rowIndex < 2 ? 'infotip-bottom' : ''\"") == 3


def test_history_grouped_view_keeps_lazy_item_count_and_loading_row() -> None:
    """Divergence history-ui-D02: the rebuild keeps the server item_count and the
    'Loading group details...' row instead of the server template's items.length."""
    html = _history_html()

    assert "{{ row.group.itemCount }}" in html
    assert "{{ dest.name }}: {{ dest.count }}/{{ row.group.itemCount }}" in html
    assert "row.type === 'loading'" in html
    assert "Loading group details..." in html


def test_history_shows_database_errors_instead_of_an_empty_history() -> None:
    """A database error reported by the history routes (error status with a detail message,
    or an empty payload carrying `error`) is shown to the user, never rendered as 'No uploads'."""
    js = _history_js()

    assert "function throwIfHistoryError(result) {" in js
    assert "function historyErrorMessage(error, fallback) {" in js
    for needle in (
        "throwIfHistoryError(result);",
        "throwIfHistoryError(await this.apiFetch('/api/uploads/history?limit=100'))",
        "throwIfHistoryError(await this.apiFetch(`/api/uploads/history/${job.job_id}/uploads`))",
        "throwIfHistoryError(await this.apiFetch(`/api/uploads/grouped/items?${params}`))",
        "throwIfHistoryError(await this.apiDelete(`/api/uploads/item/${encodeURIComponent(itemName)}`))",
        "throwIfHistoryError(await this.apiPost('/api/uploads/item/bulk-delete', { item_names: selected }))",
        "uploadsError: '',",
        "this.uploadsError = e.message || 'Unknown error';",
        "if (this.uploadsError) return historyErrorMessage({ message: this.uploadsError }, 'Could not load upload history');",
        "historyErrorMessage(e, 'Failed to load uploads')",
        "historyErrorMessage(e, 'Failed to delete item')",
        "historyErrorMessage(e, 'Failed to delete items')",
        "historyErrorMessage(e, 'Failed to delete job')",
        "historyErrorMessage(e, 'Failed to delete jobs')",
    ):
        assert needle in js, needle
    assert js.count("throwIfHistoryError(result);") == 2
    assert js.count("throwIfHistoryError(await this.apiFetch('/api/uploads/history', {") == 2

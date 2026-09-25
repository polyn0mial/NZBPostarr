"""Regression tests for the wave 2 UI integration of the live server's fixes."""

import json
import shutil
import subprocess

import pytest

from tests.conftest import _read_repo_text


def _page_base_js() -> str:
    return _read_repo_text("webui", "assets", "js", "page-base.js").replace("\r\n", "\n")


def _run_node(tmp_path, name: str, source: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    completed = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(completed.stdout)


def _strip_exports(source: str) -> str:
    return source.replace("export const ", "const ").replace("export function ", "function ")


def test_audiobook_and_ebook_items_only_match_their_own_category(tmp_path) -> None:
    # page-base.js re-exports these from shared/category.js, where they now live.
    category_js = _read_repo_text("webui", "assets", "js", "shared", "category.js").replace("\r\n", "\n")
    start = category_js.index("function findCategoryMatch(")
    end = category_js.index("\n/**\n * Map an upload category id back", start)
    helpers = _strip_exports(category_js[start:end])

    result = _run_node(
        tmp_path,
        "itype.mjs",
        f"{helpers}\n"
        "const cats = (...ids) => ids.map((id) => ({ id }));\n"
        "console.log(JSON.stringify({\n"
        "    audioBooksOnly: itypeToCategory('Audiobook', cats('books', 'ebooks')),\n"
        "    audioKeyword: itypeToCategory('Audiobook', cats('books', 'audiobook-hd')),\n"
        "    audioExact: itypeToCategory('Audiobook', cats('audiobook-hd', 'audiobooks')),\n"
        "    ebookBooksOnly: itypeToCategory('Ebook', cats('books', 'audiobooks')),\n"
        "    ebookExact: itypeToCategory('Ebook', cats('books', 'ebooks')),\n"
        "    ebookKeyword: itypeToCategory('Ebook', cats('books', 'ebook-epub')),\n"
        "}));\n",
    )

    assert result == {
        "audioBooksOnly": "",
        "audioKeyword": "audiobook-hd",
        "audioExact": "audiobooks",
        "ebookBooksOnly": "",
        "ebookExact": "ebooks",
        "ebookKeyword": "ebook-epub",
    }


def test_each_page_keeps_the_category_palette_the_server_serves(tmp_path) -> None:
    page_base_js = _page_base_js()
    start = page_base_js.index("export const categoryMeta = {")
    end = page_base_js.index("// Revision of the Queue page's saved-state key", start)
    palette = _strip_exports(page_base_js[start:end])
    hex_start = page_base_js.index("const DEFAULT_CATEGORY_HEX = {")
    hex_end = page_base_js.index("};", hex_start) + 2

    result = _run_node(
        tmp_path,
        "palette.mjs",
        f"{palette}\n{page_base_js[hex_start:hex_end]}\n"
        "const pick = (meta) => Object.fromEntries(Object.entries(meta).map(([id, m]) => [id, [m.color, m.badgeClass]]));\n"
        "console.log(JSON.stringify({ base: pick(categoryMeta), queue: pick(queueCategoryMeta), hex: DEFAULT_CATEGORY_HEX }));\n",
    )

    # Dashboard, History and Settings (server dist bundles and settings defaults).
    base = {cat: color for cat, (color, _badge) in result["base"].items()}
    assert base["tv"] == "cyan" and result["hex"]["tv"] == "#22d3ee"
    assert base["misc"] == "amber" and result["hex"]["misc"] == "#f59e0b"
    assert base["music"] == "green" and result["hex"]["music"] == "#22c55e"
    assert base["books"] == "blue" and result["hex"]["books"] == "#3b82f6"
    assert base["apps"] == "orange" and result["hex"]["apps"] == "#f97316"
    assert result["base"]["disc"] == ["slate", "bg-slate-500/15 text-slate-400"]

    # Queue page (pages/queue/ uses its own categoryMeta).
    queue = result["queue"]
    assert queue["tv"] == ["cyan", "bg-cyan-500/15 text-cyan-400"]
    assert queue["misc"] == ["orange", "bg-orange-500/15 text-orange-400"]
    assert queue["music"] == ["blue", "bg-blue-500/15 text-blue-400"]
    assert queue["books"] == ["emerald", "bg-emerald-500/15 text-emerald-400"]
    assert queue["apps"] == ["red", "bg-red-500/15 text-red-400"]
    assert queue["disc"] == ["slate", "bg-[#E0E0E0] text-[#2A2A2A] border-[#B9B9B9]"]
    assert queue["movies"] == result["base"]["movies"]
    assert queue["anime"] == result["base"]["anime"]


def test_queue_modules_use_the_queue_palette() -> None:
    for module in ("pending-categories.js",):
        source = _read_repo_text("webui", "assets", "js", "pages", "queue", module)
        assert "queueCategoryMeta as categoryMeta" in source, module


def test_queue_page_keeps_the_saved_state_key_of_the_live_server() -> None:
    page_base_js = _page_base_js()

    assert "export const QUEUE_PERSIST_REV = '20260805_queue_hotfix_r5';" in page_base_js
    assert "const persistRev = pageTitle === 'Queue' ? QUEUE_PERSIST_REV : (root.dataset.persistRev || '');" in page_base_js
    assert "const storageId = persistRev ? `${baseStorageId}_${persistRev}` : baseStorageId;" in page_base_js
    assert "const persistKey = `nzbpostarr_persist_${storageId}`;" in page_base_js


def test_bulk_selection_off_means_manual_selection_only(tmp_path) -> None:
    methods = _read_repo_text("webui", "assets", "js", "pages", "queue", "pending-filters.js").replace("\r\n", "\n")
    start = methods.index("findFolderEntryForGroup(group) {")
    end = methods.index("/**\n     * Backward-compatible count/has helpers", start)

    result = _run_node(
        tmp_path,
        "manual.mjs",
        "const page = {\n"
        "    normalizePathKey(value) { return String(value || '').replace(/[\\\\/]+$/, ''); },\n"
        "    folderPathEntries: [\n"
        "        { path: '/media/manual/', allow_bulk_selection: false },\n"
        "        { path: '/media/bulk', allow_bulk_selection: true },\n"
        "        { path: '/media/legacy', manual_select_only: true },\n"
        "    ],\n"
        f"{methods[start:end]}"
        "};\n"
        "console.log(JSON.stringify([\n"
        "    page.isGroupManualSelectionOnly({ folder_path: '/media/manual' }),\n"
        "    page.isGroupManualSelectionOnly({ key: '/media/bulk' }),\n"
        "    page.isGroupManualSelectionOnly({ key: '/media/legacy' }),\n"
        "    page.isGroupManualSelectionOnly({ key: '/media/unknown' }),\n"
        "]));\n",
    )

    assert result == [True, False, False, False]


def test_no_page_reads_a_manual_select_only_key() -> None:
    for parts in (
        ("webui", "queue.html"),
        ("webui", "assets", "js", "pages", "queue", "pending-filters.js"),
        ("webui", "assets", "js", "dist", "pages", "queue", "index.js"),
    ):
        assert "manual_select_only" not in _read_repo_text(*parts), "/".join(parts)


def test_nzb_life_rebrand_and_curl_guidance_in_docs() -> None:
    assert '("su", "NZB.Life", "https://nzb.life"),' in _read_repo_text("setup.py")
    readme = _read_repo_text("README.md")
    assert "| NZB.Life " in readme and "(formerly NZB.su)" in readme  # the indexer table row
    agents = _read_repo_text("AGENTS.md")
    assert "_curl_redirect_result" not in agents
    assert "judged by the final page" in agents

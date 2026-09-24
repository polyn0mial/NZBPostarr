"""Regression tests for the shared UI / indexer fixes ported from the live server."""

import json
import shutil
import subprocess

import pytest
import yaml

from tests.support import IndexerDefinition, _read_repo_text


def _page_base_js() -> str:
    return _read_repo_text("webui", "assets", "js", "page-base.js")


def test_su_indexer_is_rebranded_to_nzb_life_but_keeps_its_id() -> None:
    data = yaml.safe_load(_read_repo_text("indexers", "su.yaml"))
    indexer = IndexerDefinition(**data)

    assert indexer.id == "su"
    assert indexer.name == "NZB.Life"
    assert indexer.website == "https://nzb.life"
    assert indexer.favicon_url == "https://www.nzb.life/templates/dev/images/icons/favicon.ico"
    assert "formerly NZB.su" in indexer.description


def test_docs_and_indexer_guides_show_nzb_life() -> None:
    docs_html = _read_repo_text("webui", "docs.html")
    guides_html = _read_repo_text("webui", "indexer-guides.html")

    assert "(NZBGeek, NZB.Life, OMG, NZBPlanet, NZBs.in, NZBSlug)" in docs_html
    assert '<span class="text-notion-text-primary font-medium">NZB.su</span>' not in docs_html
    assert "{ id: 'nzbsu', title: 'NZB.Life (formerly NZB.su)', shortName: 'NZB.Life'," in guides_html
    assert "nzb.life → Profile → API Key" in guides_html
    assert "NZB.su / NZB.life" not in guides_html


def test_category_colours_follow_the_served_page_base() -> None:
    page_base_js = _page_base_js()
    core_css = _read_repo_text("webui", "assets", "css", "core.css")

    # Wave 2 ruling: each page keeps the palette the server serves it (the
    # server's dist/page-base.js bundle, TV green, is loaded by no page).
    assert "tv: { id: 'tv', label: 'TV', icon: 'tv', color: 'cyan', badgeClass: 'bg-cyan-500/15 text-cyan-400' }" in page_base_js
    assert "misc: { color: 'orange', badgeClass: 'bg-orange-500/15 text-orange-400' }," in page_base_js
    assert "'emerald': { bg: 'bg-emerald-500/15'" in page_base_js
    assert "'slate': { bg: 'bg-slate-500/15'" in page_base_js
    assert ".disc-cat-badge {" in core_css
    assert "overflow-y: clip;" in core_css


def test_infotip_styles_include_base_arrow_and_wrapping() -> None:
    css = _read_repo_text("webui", "assets", "css", "tailwind-input.css")

    base_arrow = css.index("    .infotip-arrow {")
    assert base_arrow < css.index("    .infotip-bottom .infotip-arrow {")
    assert "min-width: 120px;" in css
    assert "max-width: 300px;" in css
    assert "overflow-wrap: anywhere;" in css
    assert "select.rounded-full {" in css
    assert '@source inline("bg-slate-500/30 text-slate-300 border-emerald-500/50 border-notion-warning/50 bg-emerald-500/15");' in css


def test_active_job_name_and_counter_use_server_wording() -> None:
    page_base_js = _page_base_js()

    assert "both: 'Both'," in page_base_js
    assert "anime: 'Anime'," in page_base_js
    assert "disc: 'DISC'," in page_base_js
    assert "return `${base} - ${targetCount} item${targetCount === 1 ? '' : 's'}`;" in page_base_js
    assert "return Number(job.target_path_count || 0);" in page_base_js
    assert "return `${remaining} not uploaded`;" in page_base_js
    assert "return `${current} / ${total} items`;" in page_base_js
    assert "Selected Upload" not in page_base_js


def test_category_appearance_state_migrates_legacy_v1_storage(tmp_path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    page_base_js = _page_base_js().replace("\r\n", "\n")
    start = page_base_js.index("export const CATEGORY_APPEARANCE_STORAGE_KEY")
    end = page_base_js.index("\n/**\n * Get the label for a category id", start)
    helpers = page_base_js[start:end].replace("export const ", "const ").replace("export function ", "function ")

    legacy = {
        "categories": {
            "tv": {"color": "#abc", "selected_save_id": 7, "saves": [{"id": 7, "name": "Mine", "color": "123456"}, {"name": "no id"}]},
            "music": "not an object",
        }
    }
    script = tmp_path / "appearance.mjs"
    script.write_text(
        "const store = new Map();\n"
        "globalThis.localStorage = {\n"
        "    getItem: (key) => (store.has(key) ? store.get(key) : null),\n"
        "    setItem: (key, value) => store.set(key, String(value)),\n"
        "    removeItem: (key) => store.delete(key),\n"
        "};\n"
        f"store.set('nzbpostarr_category_appearance_v1', {json.dumps(json.dumps(legacy))});\n"
        f"{helpers}\n"
        "const loaded = loadCategoryAppearanceState();\n"
        "console.log(JSON.stringify({\n"
        "    loaded,\n"
        "    stored: JSON.parse(store.get(CATEGORY_APPEARANCE_STORAGE_KEY)),\n"
        "    legacyLeft: store.has('nzbpostarr_category_appearance_v1'),\n"
        "    movies: getCategoryAppearanceEntry('movies', loaded),\n"
        "    rgba: hexToRgba('#22d3ee', 0.5),\n"
        "}));\n",
        encoding="utf-8",
    )
    completed = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(completed.stdout)

    assert result["loaded"] == result["stored"]
    assert result["legacyLeft"] is False
    assert result["loaded"]["version"] == 2
    assert result["loaded"]["categories"] == {
        "tv": {
            "color": "#AABBCC",
            "selected_save_id": "7",
            "saves": [{"id": "7", "name": "Mine", "color": "#123456", "created_at": None}],
        }
    }
    assert result["movies"] == {"color": "#A855F7", "selected_save_id": "", "saves": []}
    assert result["rgba"] == "rgba(34, 211, 238, 0.5)"

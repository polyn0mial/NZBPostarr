"""Settings page: source-text pins and the /api/settings fields the page reads without a fallback."""

import asyncio
import re
from pathlib import Path

import api.settings as settings_api
import core.config as config_mod
from tests.webui._source import REPO_ROOT

SETTINGS_JS_DIR = REPO_ROOT / "webui" / "assets" / "js" / "pages" / "settings"
SETTINGS_PARTIALS_DIR = REPO_ROOT / "webui" / "partials" / "settings"


def _settings_js() -> str:
    """The settings page script: pages/settings/index.js plus its responsibility modules."""
    parts = [SETTINGS_JS_DIR / "index.js"] + sorted(p for p in SETTINGS_JS_DIR.glob("*.js") if p.name != "index.js")
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)


def _settings_html() -> str:
    return (REPO_ROOT / "webui" / "settings.html").read_text(encoding="utf-8")


def _settings_partials() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SETTINGS_PARTIALS_DIR.glob("*.html")))


def test_settings_page_source_uses_server_keys_and_layout() -> None:
    settings_js = _settings_js()
    settings_html = _settings_html()
    partials = _settings_partials()

    for legacy_key in ("ignore_non_video", "ignore_extras", "require_sxxexx"):
        assert legacy_key not in settings_js
    for needle in (
        "key: 'ignore_non_episode'",
        "key: 'require_episode'",
        "require_resolution: false,",
        "category_appearance_profiles: data.ui.category_appearance_profiles,",
        "backup_folder: this.settings.folders.backup_folder,",
        "'/api/system/backup/create'",
    ):
        assert needle in settings_js, needle

    # The Manual selection only toggle is PM's allow_bulk_selection flag, inverted.
    assert "toggleBtnClassDirect(!fp.allow_bulk_selection)" in partials
    assert "fp.allow_bulk_selection === false" in partials
    assert "manual_select_only" not in settings_html + partials

    # Every section sits inside a tab wrapper; the two general wrappers must balance.
    assert settings_html.count("<div v-show=\"activeSettingsTab === 'general'\">") == 2
    content = settings_html.split("{% block content %}", 1)[1].split("{% endblock %}", 1)[0]
    assert len(re.findall(r"<div\b", content)) == content.count("</div>")
    for partial in SETTINGS_PARTIALS_DIR.glob("*.html"):
        text = partial.read_text(encoding="utf-8")
        assert len(re.findall(r"<div\b", text)) == text.count("</div>"), partial.name


def test_settings_html_includes_every_partial_and_the_split_entry() -> None:
    settings_html = _settings_html()
    included = set(re.findall(r'\{% include "partials/settings/([^"]+)" %\}', settings_html))

    assert included == {p.name for p in SETTINGS_PARTIALS_DIR.glob("*.html")}
    assert "/assets/js/dist/pages/settings/index.js?t=" in settings_html
    assert not (REPO_ROOT / "webui" / "assets" / "js" / "pages" / "settings.js").exists()


# Every /api/settings field the page reads without a `||`/`??` fallback (pages/settings/index.js).
# None marks an Optional config field: the key must be present, its value may be null.
_FIELDS_READ_WITHOUT_FALLBACK = {
    "destinations": {"enable_backfill": bool, "enable_duplicate_bypass": bool},
    "processing": {
        "verbose": bool,
        "process_tv_episodes": bool,
        "enable_duplicate_checking": bool,
        "enable_anime_checking": bool,
        "item_limit_per_category": None,
        "folder_size_limit_gb": None,
        "folder_size_limit_enabled": bool,
        "file_size_limit_gb": None,
        "file_size_limit_enabled": bool,
        "dynamic_packs": bool,
    },
    "ui": {
        "dashboard_stats_enabled": bool,
        "stats_page_enabled": bool,
        "ui_refresh_seconds": int,
        "dashboard_stats_modules": list,
        "category_appearance_profiles": dict,
    },
    "auth": {"enable_password": bool, "web_username": str},
    "upload": {
        "poster_name": str,
        "poster_email": str,
        "rar_size": str,
        "article_size": str,
        "include_readme": bool,
        "upload_max_retries": int,
        "upload_retry_delay_seconds": int,
    },
    "folders": {"base_folder": str, "backup_folder": str, "folder_paths": list},
}


def test_api_settings_returns_every_field_the_page_reads_without_fallback(tmp_path: Path, monkeypatch) -> None:
    defaults_path = Path(config_mod.__file__).with_name("config.defaults.yaml")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(defaults_path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("NZBPOSTARR_CONFIG", str(config_path))
    conf = config_mod.load_config()
    monkeypatch.setattr(settings_api, "get_config", lambda: conf)

    data = asyncio.run(settings_api.get_current_settings())

    for section, fields in _FIELDS_READ_WITHOUT_FALLBACK.items():
        for field, kind in fields.items():
            assert field in data[section], f"{section}.{field}"
            if kind is not None:
                assert isinstance(data[section][field], kind), f"{section}.{field}"
    for field, kind in (("api_keys", dict), ("usernames", dict), ("nntp_servers", list), ("categories", list)):
        assert isinstance(data[field], kind), field
    for indexer in data["indexers"]:
        for prefix in ("enable", "backfill", "priority"):
            assert isinstance(data["destinations"][f"{prefix}_{indexer['id']}"], bool)

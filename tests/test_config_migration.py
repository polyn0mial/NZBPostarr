"""Legacy config spellings all load to the same Config (core.config._migrate_legacy_config)."""

from __future__ import annotations

from typing import Any, Dict

import yaml

from core import config as config_mod
from core.config import Config, StatsFeatures
from core.redaction import SECRET_MASK
from logic import settings as settings_service


def _defaults() -> Dict[str, Any]:
    data = yaml.safe_load(config_mod.get_defaults_config_path().read_text(encoding="utf-8")) or {}
    data.setdefault("api_keys", {})
    data.setdefault("usernames", {})
    data["base_folder"] = "/srv/usenet"
    return data


def _current() -> Dict[str, Any]:
    data = _defaults()
    data.pop("poster", None)
    data.update(
        {
            "poster_name": "Poster",
            "poster_email": "poster@example.com",
            "folder_paths": [
                {"path": "/srv/movies"},
                {"path": "/srv/drop", "category": "external"},
            ],
            "tv_pack_ignore": {
                "enabled": True,
                "ignore_non_episode": False,
                "require_episode": True,
                "require_resolution": False,
                "require_source": True,
            },
        }
    )
    for key in ("movies_folder", "tv_folder", "misc_folder", "external_folder", "external_folders"):
        data.pop(key, None)
    return data


def _legacy() -> Dict[str, Any]:
    data = _defaults()
    for key in ("poster_name", "poster_email", "folder_paths", "external_folders"):
        data.pop(key, None)
    data.update(
        {
            "poster": "Poster <poster@example.com>",
            "movies_folder": "/srv/movies",
            "external_folder": "/srv/drop",
            "tv_pack_ignore": {
                "enabled": True,
                "ignore_non_video": False,
                "require_sxxexx": True,
                "require_resolution": False,
                "require_source": True,
            },
        }
    )
    return data


def _dump(conf: Config) -> Dict[str, Any]:
    return conf.model_dump(by_alias=True)


def test_legacy_config_loads_to_the_same_config_as_current() -> None:
    legacy = Config(**_legacy())
    current = Config(**_current())

    assert legacy.poster_name == "Poster"
    assert legacy.poster_email == "poster@example.com"
    assert legacy.tv_pack_ignore == current.tv_pack_ignore
    assert [(fp["path"], fp["category"]) for fp in legacy.get_folder_path_entries()] == [
        ("/srv/movies", "auto"),
        ("/srv/drop", "auto"),
    ]
    assert _dump(legacy) == _dump(current)
    assert legacy.folder_paths == current.folder_paths


def test_current_tv_pack_ignore_key_wins_over_its_legacy_spelling() -> None:
    data = _current()
    data["tv_pack_ignore"] = {"ignore_non_episode": True, "ignore_extras": False, "require_sxxexx": False}

    rules = Config(**data).tv_pack_ignore

    assert rules == {"ignore_non_episode": True, "require_episode": False}


def test_pre_port_config_gets_neutral_port_defaults() -> None:
    data = _current()
    for key in (
        "backup_folder",
        "pending_external_group_order",
        "pending_external_group_order_locked",
        "category_appearance_profiles",
    ):
        data.pop(key, None)

    conf = Config(**data)

    assert conf.backup_folder == config_mod.APP_ROOT / "backups"
    assert conf.pending_external_group_order == []
    assert conf.pending_external_group_order_locked is False
    assert conf.category_appearance_profiles == {}
    assert all(fp["allow_bulk_selection"] is True for fp in conf.get_folder_path_entries())


def test_stats_features_follow_the_flags() -> None:
    data = _current()
    data.update(stats_page_enabled=False, dashboard_stats_enabled=True, dashboard_stats_modules=["cpu"])
    features = Config(**data).stats_features

    assert features == StatsFeatures(stats_page=False, dashboard_modules=("cpu",), dashboard_enabled=True)
    assert (features.dashboard, features.history, features.connections) == (True, True, False)

    data.update(dashboard_stats_modules=[" ", "net_errors"])
    features = Config(**data).stats_features
    assert features.dashboard_modules == ("net_errors",)
    assert features.connections is True


def test_masked_settings_round_trip_keeps_the_secret() -> None:
    data = _current()
    data["api_keys"] = {"geek": "real-key", "blank": ""}
    data["web_password"] = "real-web-password"
    conf = Config(**data)

    view = settings_service.flat_config_view(conf)
    assert view["api_keys"] == {"geek": SECRET_MASK, "blank": ""}
    assert view["web_password"] == SECRET_MASK

    merged = settings_service.merge_masked_secret_updates(
        {"api_keys": view["api_keys"], "web_password": view["web_password"]},
        conf,
    )
    assert merged["api_keys"] == {"geek": "real-key", "blank": ""}
    assert merged["web_password"] == "real-web-password"

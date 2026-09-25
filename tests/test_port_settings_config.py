# ruff: noqa: F403,F405

"""Settings/config keys ported from the live server (settings-config area)."""

from tests.support import *


def _load_config_from(tmp_path: Path, monkeypatch, overrides: dict | None = None) -> Config:
    defaults_path = Path(config_mod.__file__).with_name("config.defaults.yaml")
    data = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    data.update(overrides or {})
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("NZBPOSTARR_CONFIG", str(config_path))
    return config_mod.load_config()


def test_defaults_declare_server_keys(tmp_path, monkeypatch) -> None:
    conf = _load_config_from(tmp_path, monkeypatch)

    assert conf.tv_pack_ignore == {
        "enabled": True,
        "ignore_non_episode": True,
        "require_episode": True,
        "require_resolution": False,
        "require_source": True,
    }
    assert conf.backup_folder == config_mod.APP_ROOT / "backups"
    assert conf.pending_external_group_order == []
    assert conf.pending_external_group_order_locked is False
    assert conf.category_appearance_profiles == {}


def test_model_defaults_apply_when_keys_are_missing(tmp_path, monkeypatch) -> None:
    defaults_path = Path(config_mod.__file__).with_name("config.defaults.yaml")
    data = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    for key in (
        "tv_pack_ignore",
        "backup_folder",
        "pending_external_group_order",
        "pending_external_group_order_locked",
        "category_appearance_profiles",
    ):
        data.pop(key, None)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("NZBPOSTARR_CONFIG", str(config_path))

    conf = config_mod.load_config()

    assert conf.tv_pack_ignore == config_mod.DEFAULT_TV_PACK_IGNORE
    assert conf.tv_pack_ignore is not config_mod.DEFAULT_TV_PACK_IGNORE
    assert conf.backup_folder == config_mod.APP_ROOT / "backups"
    assert conf.pending_external_group_order == []
    assert conf.pending_external_group_order_locked is False
    assert conf.category_appearance_profiles == {}


def test_legacy_tv_pack_ignore_keys_map_to_server_keys(tmp_path, monkeypatch) -> None:
    conf = _load_config_from(
        tmp_path,
        monkeypatch,
        {
            "tv_pack_ignore": {
                "enabled": True,
                "ignore_non_video": False,
                "ignore_extras": False,
                "require_sxxexx": False,
                "require_resolution": True,
                "require_source": False,
            }
        },
    )

    assert conf.tv_pack_ignore == {
        "enabled": True,
        "require_resolution": True,
        "require_source": False,
        "ignore_non_episode": False,
        "require_episode": False,
    }


def test_current_tv_pack_ignore_key_wins_over_legacy_spelling(tmp_path, monkeypatch) -> None:
    conf = _load_config_from(
        tmp_path,
        monkeypatch,
        {
            "tv_pack_ignore": {
                "ignore_non_video": False,
                "ignore_non_episode": True,
                "require_sxxexx": True,
                "require_episode": False,
            }
        },
    )

    assert conf.tv_pack_ignore == {"ignore_non_episode": True, "require_episode": False}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("backups", config_mod.APP_ROOT / "backups"),
        ("", config_mod.APP_ROOT / "backups"),
        (None, config_mod.APP_ROOT / "backups"),
        ("~/nzb-backups", Path("~/nzb-backups").expanduser()),
    ],
)
def test_backup_folder_resolves_relative_and_blank_values(tmp_path, monkeypatch, raw, expected) -> None:
    conf = _load_config_from(tmp_path, monkeypatch, {"backup_folder": raw})

    assert conf.backup_folder == expected


def test_backup_folder_keeps_an_explicit_absolute_path(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "operator-backups"

    conf = _load_config_from(tmp_path, monkeypatch, {"backup_folder": str(explicit)})

    assert conf.backup_folder == explicit


def test_save_config_round_trips_ported_keys(tmp_path, monkeypatch) -> None:
    # save_config installs the reloaded config globally; restore it afterwards.
    monkeypatch.setattr(config_mod, "_GLOBAL_CONFIG", config_mod._GLOBAL_CONFIG)
    monkeypatch.setattr(config_mod, "_CONFIG_MTIME_NS", config_mod._CONFIG_MTIME_NS)
    _load_config_from(tmp_path, monkeypatch)
    profiles = {
        "tv": {
            "color": "#22C55E",
            "selected_save_id": "tv-1",
            "saves": [{"id": "tv-1", "name": "Saved 1", "color": "#22C55E", "created_at": None}],
        }
    }
    explicit = tmp_path / "operator-backups"

    assert config_mod.save_config(
        {
            "category_appearance_profiles": profiles,
            "backup_folder": str(explicit),
            "pending_external_group_order": ["b", "a"],
            "pending_external_group_order_locked": True,
        }
    )

    conf = config_mod.load_config()
    assert conf.category_appearance_profiles == profiles
    assert conf.backup_folder == explicit
    assert conf.pending_external_group_order == ["b", "a"]
    assert conf.pending_external_group_order_locked is True


def test_public_defaults_carry_no_operator_home_path() -> None:
    defaults_text = _read_repo_text("core", "config.defaults.yaml")
    config_text = _read_repo_text("core", "config.py")
    webui = Path(__file__).resolve().parents[1] / "webui"
    settings_sources = [webui / "settings.html"]
    settings_sources += sorted((webui / "partials" / "settings").glob("*.html"))
    settings_sources += sorted((webui / "assets" / "js" / "pages" / "settings").glob("*.js"))

    for text in (defaults_text, config_text, *(p.read_text(encoding="utf-8") for p in settings_sources)):
        assert "/home/" not in text


def test_setup_wizard_lists_nzb_life() -> None:
    setup_text = _read_repo_text("setup.py")

    assert '("su", "NZB.Life", "https://nzb.life")' in setup_text
    assert "https://nzb.su" not in setup_text

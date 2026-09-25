# ruff: noqa: F403,F405

"""Guards for the W2-B1 deletions: removed routes, config keys and shims stay removed without breaking callers."""

from tests.support import *

from logic import queueing_base
from tests.characterization.test_surface import _route_table

_REMOVED_ROUTES = {
    ("POST", "/api/uploads/preview"),
    ("GET", "/api/tests/ping"),
    ("POST", "/api/pending/preview-upload"),
}

_DOCUMENTED_PATHS = {
    "/api/stats/summary",
    "/api/stats/mini",
    "/api/stats/history/record",
    "/api/settings/reset",
    "/api/settings/config",
    "/api/dashboard/database-health",
    "/api/tests/health",
    "/api/dashboard/health",
}


def test_undocumented_routes_without_consumers_are_removed() -> None:
    routes = {(method, path) for methods, path in _route_table() for method in methods}

    assert routes.isdisjoint(_REMOVED_ROUTES)
    assert _DOCUMENTED_PATHS <= {path for _method, path in routes}


def test_old_config_with_removed_keys_still_loads(tmp_path, monkeypatch) -> None:
    movies_root = tmp_path / "movies"
    tv_root = tmp_path / "tv"
    external_root = tmp_path / "external"
    defaults_path = Path(config_mod.__file__).with_name("config.defaults.yaml")
    data = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
    data.update(
        {
            "medianfo": "/opt/mediainfo/mediainfo",
            "static_cache_max_age": 3600,
            "folder_paths": [],
            "movies_folder": str(movies_root),
            "tv_folder": str(tv_root),
            "misc_folder": None,
            "external_folder": str(external_root),
        }
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("NZBPOSTARR_CONFIG", str(config_path))

    conf = config_mod.load_config()

    assert "medianfo" not in Config.model_fields
    assert "static_cache_max_age" not in Config.model_fields
    # The validator folds the legacy folder fields into folder_paths and nulls them,
    # so the scan roots survive without any legacy fallback in the scanner.
    assert conf.movies_folder is None and conf.tv_folder is None
    assert [entry["path"] for entry in conf.get_folder_path_entries()] == [
        str(movies_root),
        str(tv_root),
        str(external_root),
    ]
    assert pending_roots.get_configured_category_folders(conf, include_external=True) == [
        ("external", movies_root),
        ("external", tv_root),
        ("external", external_root),
    ]


def test_queueing_uses_the_classifier_season_folder_predicate() -> None:
    assert queueing_base.looks_like_generic_tv_season_folder is classify_names.looks_like_generic_tv_season_folder

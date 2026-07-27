# ruff: noqa: F403,F405

"""Tests for indexer profile ergonomics: the opt-in 'profile: newznab' shortcut,
closed-set validation of method/auth.method, and example/template file
exclusion from the live indexer loader."""

from tests.support import *

def test_shipped_indexers_load_with_unchanged_auth_method_and_categories() -> None:
    expected = {
        "geek": {"method": "POST", "auth_method": "query_param", "tv": "5040", "movie": "2040"},
        "planet": {"method": "POST", "auth_method": "query_param", "tv": "5040", "movie": "2040"},
        "su": {"method": "POST", "auth_method": "query_param", "tv": "5040", "movie": "2040"},
        "in": {"method": "POST", "auth_method": "header", "tv": "tv-shows", "movie": "movies"},
        "slug": {"method": "POST", "auth_method": "header", "tv": "", "movie": ""},
        "omg": {"method": "CURL", "auth_method": "curl_url", "tv": "tv", "movie": "movie"},
    }

    registry_mod.reload_indexers()
    registry = registry_mod.get_registry()

    assert set(expected) <= set(registry.ids())

    for indexer_id, expectations in expected.items():
        indexer = registry.get(indexer_id)
        assert indexer is not None, indexer_id
        assert indexer.method == expectations["method"], indexer_id
        assert indexer.auth.method == expectations["auth_method"], indexer_id
        assert indexer.categories.tv == expectations["tv"], indexer_id
        assert indexer.categories.movie == expectations["movie"], indexer_id

def test_example_file_is_not_loaded_as_live_indexer() -> None:
    registry_mod.reload_indexers()
    registry = registry_mod.get_registry()

    ids = set(registry.ids())
    assert "my_newznab_indexer" not in ids
    for indexer_id in ids:
        assert "example" not in indexer_id

    # The example file itself must still parse as a valid indexer definition
    # (it is a real, loadable profile once copied) - only the loader excludes it.
    example_data = yaml.safe_load(_read_repo_text("indexers", "newznab.example.yaml"))
    example_indexer = IndexerDefinition(**example_data)
    assert example_indexer.id == "my_newznab_indexer"
    assert example_indexer.profile == "newznab"

def test_invalid_method_value_is_rejected() -> None:
    with pytest.raises(Exception):
        IndexerDefinition(
            id="bad-method",
            name="Bad Method",
            submit_url="https://example.invalid/api",
            method="PSOT",
        )

def test_invalid_auth_method_value_is_rejected() -> None:
    with pytest.raises(Exception):
        IndexerDefinition(
            id="bad-auth",
            name="Bad Auth",
            submit_url="https://example.invalid/api",
            auth=AuthConfig(method="Header"),
        )

def test_profile_newznab_matches_url_suffix_auto_detection(tmp_path, monkeypatch) -> None:
    common_kwargs = dict(
        auth=AuthConfig(method="query_param", api_key_param="apikey"),
        name_param="name",
        categories=registry_mod.CategoryMapping(tv="5040", movie="2040", misc="5000", default="5000"),
        success=registry_mod.SuccessPatterns(text_patterns=["OK"]),
    )

    explicit_profile_indexer = IndexerDefinition(
        id="profile-newznab",
        name="Profile Newznab",
        submit_url="https://example.invalid/submit",  # deliberately does NOT end in "/api"
        profile="newznab",
        **common_kwargs,
    )
    legacy_heuristic_indexer = IndexerDefinition(
        id="legacy-heuristic",
        name="Legacy Heuristic",
        submit_url="https://example.invalid/api",  # relies on the "/api" suffix heuristic
        **common_kwargs,
    )

    for indexer in (explicit_profile_indexer, legacy_heuristic_indexer):
        seen = _capture_submit_request(monkeypatch, text="OK")
        nzb_file = _make_sample_nzb(tmp_path / indexer.id)

        ok, status, _reason = submit_to_indexer(
            indexer=indexer,
            rls_name="Show.Name.S01E01.1080p.WEB-DL",
            nzb_path=nzb_file,
            config=_DummySubmitConfig(),
            cat="tv",
        )

        assert ok is True, indexer.id
        assert status == "success", indexer.id
        assert seen["params"]["t"] == "nzbadd", indexer.id
        assert seen["params"]["apikey"] == "abc123", indexer.id
        assert seen["params"]["cat"] == "5040", indexer.id
        assert seen["params"]["name"] == "Show.Name.S01E01.1080p.WEB-DL", indexer.id
        assert seen["data"]["cat"] == "5040", indexer.id
        assert seen["data"]["name"] == "Show.Name.S01E01.1080p.WEB-DL", indexer.id

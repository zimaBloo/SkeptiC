"""The model lineups and where their API keys come from."""

import os
from pathlib import Path

import pytest

from skeptic.lineups import (
    FRONTIER,
    LINEUPS,
    LOCAL,
    ROLES,
    ROUTES,
    env_file,
    get_lineup,
    load_env,
    missing_key,
)

KEY = "OPENROUTER_API_KEY"


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inspect loads the project's .env when a test runs the panel, so a real key
    # may already be in the environment.
    monkeypatch.delenv(KEY, raising=False)


def test_every_lineup_puts_a_model_on_every_role() -> None:
    for lineup in LINEUPS.values():
        assert set(lineup.models) == set(ROLES)


def test_each_frontier_critic_comes_from_a_different_lab() -> None:
    labs = [FRONTIER.models[role].split("/")[1] for role in ROLES if role != "combiner"]
    assert len(set(labs)) == len(labs)


def test_every_frontier_model_is_pinned_to_its_own_lab() -> None:
    for model in FRONTIER.models.values():
        lab = model.split("/")[1]
        route = ROUTES[model]
        assert route.provider.startswith({"x-ai": "xai", "google": "google"}.get(lab, lab))


def test_local_lineup_needs_no_key_and_frontier_needs_openrouters(no_key: None) -> None:
    assert missing_key(LOCAL) is None
    assert missing_key(FRONTIER) == KEY


def test_unknown_lineup_names_the_known_ones() -> None:
    with pytest.raises(ValueError, match="local, frontier"):
        get_lineup("cloud")


def test_key_in_the_environment_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY, "sk-or-test")
    assert missing_key(FRONTIER) is None


def test_key_in_env_file_of_the_folder_or_above_counts(tmp_path: Path, no_key: None) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert missing_key(FRONTIER, project) == KEY

    (tmp_path / ".env").write_text(f"{KEY}=sk-or-test\n", encoding="utf-8")

    assert env_file(project) == tmp_path / ".env"
    assert missing_key(FRONTIER, project) is None


def test_empty_key_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY, "")
    (tmp_path / ".env").write_text(f"{KEY}=\n", encoding="utf-8")

    assert missing_key(FRONTIER, tmp_path) == KEY


def test_loading_env_file_keeps_set_variables_and_fills_empty_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        f"{KEY}=sk-or-from-file\nSKEPTIC_TEST_SET=from-file\n", encoding="utf-8"
    )
    monkeypatch.setenv(KEY, "")
    monkeypatch.setenv("SKEPTIC_TEST_SET", "from-shell")

    load_env(tmp_path)

    assert os.environ[KEY] == "sk-or-from-file"
    assert os.environ["SKEPTIC_TEST_SET"] == "from-shell"

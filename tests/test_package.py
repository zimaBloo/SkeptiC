import skeptic


def test_version_matches_project_metadata() -> None:
    assert skeptic.__version__ == "0.1.0"

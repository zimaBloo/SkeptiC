"""The run record and the skeptic command, on mock models, offline."""

import json
from pathlib import Path

import pytest
from mock_panel import IDEA, run_panel

from skeptic.cli import main
from skeptic.critics import CRITICS, TITLES, prompts_digest
from skeptic.record import PanelRecord, record_from_log


def test_record_holds_the_idea_every_critic_and_the_combined_verdict(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, task_args={"seed": 3})

    record = record_from_log(log)

    assert record.format_version == 1
    assert record.status == "success"
    assert record.idea == IDEA
    assert [c.critic for c in record.critics] == list(CRITICS)
    assert [c.title for c in record.critics] == [TITLES[c] for c in CRITICS]
    assert all(c.verdict is not None for c in record.critics)
    assert record.combiner.combined is not None
    assert record.prompts_sha256 == prompts_digest()
    assert record.settings["seed"] == 3
    assert "idea" not in record.settings
    assert record.scores["critics_answered"] == 4.0


def test_record_has_each_calls_timing_in_order(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path)

    record = record_from_log(log)

    timings = [c.timing for c in record.critics] + [record.combiner.timing]
    assert all(t.started_at is not None and t.finished_at is not None for t in timings)
    starts = [t.started_at for t in timings]
    assert starts == sorted(starts)  # type: ignore[type-var]


def test_record_of_a_failed_run_says_why(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, **dict.fromkeys(CRITICS, "No."))

    record = record_from_log(log)

    assert record.status == "error"
    assert record.error is not None and "no critic returned a usable verdict" in record.error
    assert all(c.error is not None for c in record.critics)
    assert record.combiner.combined is None


def test_record_survives_a_round_trip_through_json(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path)
    record = record_from_log(log)

    assert PanelRecord.model_validate_json(record.model_dump_json()) == record


def test_export_writes_the_record_of_a_log(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path)
    out = tmp_path / "record.json"

    assert main(["export", str(log.location), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["idea"] == IDEA


def test_schema_prints_the_records_json_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0

    schema = json.loads(capsys.readouterr().out)
    assert "critics" in schema["properties"]


def test_run_writes_a_record_even_when_the_run_fails(tmp_path: Path) -> None:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")
    out = tmp_path / "record.json"
    # mockllm's default reply is not JSON, so every critic fails.
    roles = [f"--role={role}=mockllm/model" for role in [*CRITICS, "combiner"]]

    code = main(["run", str(idea), "--out", str(out), "--log-dir", str(tmp_path / "logs"), *roles])

    assert code == 1
    assert json.loads(out.read_text())["status"] == "error"


def test_run_rejects_a_missing_idea_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["run", str(tmp_path / "missing.md")])

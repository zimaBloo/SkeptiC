"""Progress events written by the panel, on mock models, offline."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from mock_panel import COMBINED, IDEA, Recorder, only_sample, run_panel

from skeptic.cli import main
from skeptic.critics import CRITICS
from skeptic.panel import PanelResult
from skeptic.progress import CriticFinished, ProgressWriter, RunFinished, read_events
from skeptic.record import record_from_log


def events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def without_time(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "time"}


def test_panel_reports_each_step_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skeptic.panel.run_together", lambda critics: False)
    progress = tmp_path / "progress.jsonl"
    log, _ = run_panel(tmp_path, task_args={"progress_file": str(progress)})

    assert log.status == "success"
    written = events(progress)
    assert [e["event"] for e in written] == [
        "run_started",
        *["critic_started", "critic_finished"] * len(CRITICS),
        "combiner_started",
        "combiner_finished",
        "run_finished",
    ]
    assert without_time(written[0]) == {
        "event": "run_started",
        "critics": list(CRITICS),
        "models": dict.fromkeys([*CRITICS, "combiner"], "mockllm/model"),
        "parallel": False,
    }
    assert without_time(written[1]) == {
        "event": "critic_started",
        "critic": "market",
        "model": "mockllm/model",
    }
    assert without_time(written[2]) == {
        "event": "critic_finished",
        "critic": "market",
        "ok": True,
        "issues": 1,
        "overall": "promising",
        "error": None,
    }
    assert [e["critic"] for e in written if e["event"] == "critic_finished"] == list(CRITICS)
    assert without_time(written[-3]) == {"event": "combiner_started", "model": "mockllm/model"}
    assert without_time(written[-2]) == {
        "event": "combiner_finished",
        "ok": True,
        "points": 1,
        "error": None,
    }
    assert without_time(written[-1]) == {"event": "run_finished", "ok": True, "error": None}


def test_critics_that_run_together_report_as_each_starts_and_ends(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    log, _ = run_panel(tmp_path, task_args={"progress_file": str(progress)})

    assert log.status == "success"
    written = events(progress)
    kinds = [e["event"] for e in written]
    assert written[0]["event"] == "run_started" and written[0]["parallel"] is True
    for critic in CRITICS:
        mine = [e["event"] for e in written if e.get("critic") == critic]
        assert mine == ["critic_started", "critic_finished"]
    last_critic = max(i for i, kind in enumerate(kinds) if kind == "critic_finished")
    assert kinds.index("combiner_started") > last_critic


def test_every_event_starts_with_its_utc_time_and_name(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    run_panel(tmp_path, task_args={"progress_file": str(progress)})

    times = [event.time for event in read_events(progress)]
    assert times == sorted(times)
    assert all(time.utcoffset() == timedelta(0) for time in times)
    for line in progress.read_text(encoding="utf-8").splitlines():
        assert list(json.loads(line))[:2] == ["time", "event"]


def test_critic_with_an_unusable_reply_is_reported_and_the_run_goes_on(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    run_panel(tmp_path, task_args={"progress_file": str(progress)}, risk="No.")

    [risk] = [
        e for e in read_events(progress) if isinstance(e, CriticFinished) and e.critic == "risk"
    ]
    assert (risk.ok, risk.issues, risk.overall) == (False, None, None)
    assert risk.error is not None and "no JSON object" in risk.error
    assert events(progress)[-1]["ok"] is True


def test_statement_repair_is_reported_inside_the_combining_step(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    repaired = json.dumps({"statement": "Competition is a major problem."})
    run_panel(
        tmp_path,
        task_args={"progress_file": str(progress)},
        combiner=Recorder([json.dumps(overstated), repaired]),
    )

    assert [e["event"] for e in events(progress)][-4:] == [
        "combiner_started",
        "repair_started",
        "combiner_finished",
        "run_finished",
    ]


def test_run_that_fails_reports_why_before_the_error_is_raised(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    log, _ = run_panel(
        tmp_path, task_args={"progress_file": str(progress)}, **dict.fromkeys(CRITICS, "No.")
    )

    assert log.status == "error"
    written = events(progress)
    assert "combiner_started" not in [e["event"] for e in written]
    assert written[-1]["event"] == "run_finished"
    assert written[-1]["ok"] is False
    assert "no critic returned a usable verdict" in written[-1]["error"]


def test_unusable_combiner_reply_is_reported_then_the_run_fails(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    log, _ = run_panel(tmp_path, task_args={"progress_file": str(progress)}, combiner="Not JSON.")

    assert log.status == "error"
    finished, run_finished = events(progress)[-2:]
    assert finished["event"] == "combiner_finished"
    assert (finished["ok"], finished["points"]) == (False, None)
    assert "no JSON object" in finished["error"]
    assert run_finished["event"] == "run_finished"
    assert run_finished["ok"] is False
    assert run_finished["error"] == finished["error"]


def test_progress_does_not_change_the_record(tmp_path: Path) -> None:
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    progress = tmp_path / "progress.jsonl"
    with_progress, _ = run_panel(tmp_path / "with", task_args={"progress_file": str(progress)})
    without_progress, _ = run_panel(tmp_path / "without")

    first, second = record_from_log(with_progress), record_from_log(without_progress)
    assert first.settings == second.settings
    assert "progress_file" not in first.settings
    assert first.combiner.combined == second.combiner.combined
    assert [c.verdict for c in first.critics] == [c.verdict for c in second.critics]
    assert first.scores == second.scores


def test_progress_file_that_cannot_be_written_does_not_stop_the_run(tmp_path: Path) -> None:
    # A folder cannot be opened for appending.
    log, _ = run_panel(tmp_path, task_args={"progress_file": str(tmp_path)})

    assert log.status == "success"
    assert only_sample(log).store_as(PanelResult).combined is not None


def test_run_command_passes_the_progress_file_on(tmp_path: Path) -> None:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")
    progress = tmp_path / "progress.jsonl"
    # mockllm's default reply is not JSON, so every critic fails.
    roles = [f"--role={role}=mockllm/model" for role in [*CRITICS, "combiner"]]

    code = main(
        [
            "run",
            str(idea),
            "--out",
            str(tmp_path / "record.json"),
            "--progress",
            str(progress),
            "--log-dir",
            str(tmp_path / "logs"),
            *roles,
        ]
    )

    assert code == 1
    written = events(progress)
    assert written[0]["event"] == "run_started"
    assert (written[-1]["event"], written[-1]["ok"]) == ("run_finished", False)


def test_reading_leaves_out_a_line_still_being_written(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    ProgressWriter(progress).write(RunFinished(ok=True, error=None))
    with progress.open("a", encoding="utf-8") as file:
        file.write('{"time": "2026-11-21T23:15:02Z", "event": "crit')

    [event] = read_events(progress)
    assert isinstance(event, RunFinished)
    assert read_events(tmp_path / "missing.jsonl") == []


def test_reading_leaves_out_a_line_cut_off_inside_a_character(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    ProgressWriter(progress).write(RunFinished(ok=False, error="Äänipankki"))
    with progress.open("ab") as file:
        file.write(b'{"time": "2026-11-21T23:15:02Z", "event": "run_finished", "error": "\xc3')

    [event] = read_events(progress)
    assert isinstance(event, RunFinished)
    assert event.error == "Äänipankki"


def test_reading_keeps_an_event_whose_text_has_a_line_separator(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    ProgressWriter(progress).write(RunFinished(ok=False, error="first\u2028second"))

    [event] = read_events(progress)
    assert isinstance(event, RunFinished)
    assert event.error == "first\u2028second"

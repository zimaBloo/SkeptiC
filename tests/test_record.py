"""The run record and the skeptic command, on mock models, offline."""

import json
from pathlib import Path

import pytest
from inspect_ai.event import ModelEvent
from inspect_ai.log import write_eval_log
from mock_panel import COMBINED, IDEA, Recorder, run_panel

from skeptic.cli import main
from skeptic.critics import CRITICS, TITLES, prompts_digest
from skeptic.record import PanelRecord, record_from_log


def test_record_holds_the_idea_every_critic_and_the_combined_verdict(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, task_args={"seed": 3})

    record = record_from_log(log)

    assert record.format_version == 2
    assert record.status == "success"
    assert record.idea == IDEA
    assert [c.critic for c in record.critics] == list(CRITICS)
    assert [c.title for c in record.critics] == [TITLES[c] for c in CRITICS]
    assert all(c.verdict is not None for c in record.critics)
    assert record.combiner.combined is not None
    assert record.prompts_sha256 == prompts_digest()
    assert record.settings == {
        "lineup": "local",
        "reasoning": "none",
        "temperature": 0.0,
        "seed": 3,
        "max_tokens": 3000,
    }
    assert record.critics_parallel is True
    assert record.scores["critics_answered"] == 4.0


def test_record_has_each_calls_timing_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("skeptic.panel.run_together", lambda critics: False)
    log, _ = run_panel(tmp_path)

    record = record_from_log(log)

    timings = [c.timing for c in record.critics] + [record.combiner.timing]
    assert all(t.started_at is not None and t.finished_at is not None for t in timings)
    starts = [t.started_at for t in timings]
    assert starts == sorted(starts)  # type: ignore[type-var]
    assert record.critics_parallel is False


def test_record_times_critics_that_ran_together_before_the_combiner(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path)

    record = record_from_log(log)

    ends = [c.timing.finished_at for c in record.critics]
    assert all(end is not None for end in ends)
    combiner_start = record.combiner.timing.started_at
    assert combiner_start is not None
    assert all(end <= combiner_start for end in ends if end is not None)


def test_record_of_a_failed_run_says_why(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, **dict.fromkeys(CRITICS, "No."))

    record = record_from_log(log)

    assert record.status == "error"
    assert record.error is not None and "no critic returned a usable verdict" in record.error
    assert all(c.error is not None for c in record.critics)
    assert record.combiner.combined is None


def test_run_command_refuses_a_lineup_whose_key_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")

    code = main(["run", str(idea), "--lineup", "frontier"])

    assert code == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()


def test_run_command_refuses_no_reasoning_on_frontier_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")

    with pytest.raises(SystemExit) as stopped:
        main(["run", str(idea), "--lineup", "frontier", "--reasoning", "none"])

    assert stopped.value.code == 2
    assert "always reason" in capsys.readouterr().err


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


def test_record_of_a_log_from_an_older_format_says_so(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "unmarked").mkdir()
    broken, _ = run_panel(tmp_path / "broken")
    unmarked, _ = run_panel(tmp_path / "unmarked")
    assert broken.samples is not None and unmarked.samples is not None
    broken.samples[0].store["PanelResult:combined"] = {"points": [{"point": "Old."}]}
    unmarked.samples[0].store["PanelResult:record_format"] = None

    for log in (broken, unmarked):
        with pytest.raises(ValueError, match="older panel code"):
            record_from_log(log)


def test_export_of_an_older_log_prints_a_message_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log, _ = run_panel(tmp_path)
    assert log.samples is not None
    log.samples[0].store["PanelResult:record_format"] = None
    write_eval_log(log, str(tmp_path / "old.eval"))

    assert main(["export", str(tmp_path / "old.eval")]) == 2
    assert "older panel code" in capsys.readouterr().err


def test_combiner_timing_spans_the_repair_call(tmp_path: Path) -> None:
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    combiner = Recorder([json.dumps(overstated), json.dumps({"statement": "A major problem."})])
    log, _ = run_panel(tmp_path, combiner=combiner)

    record = record_from_log(log)

    assert log.samples is not None
    calls = [e for e in log.samples[0].events if isinstance(e, ModelEvent) and e.role == "combiner"]
    first, last = calls
    timing = record.combiner.timing
    assert timing.started_at == first.timestamp
    assert timing.finished_at == last.completed
    assert timing.seconds == pytest.approx((first.working_time or 0) + (last.working_time or 0))
    assert record.scores["statement_repaired"] == 1.0


def test_record_says_why_a_repair_was_not_used(tmp_path: Path) -> None:
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    log, _ = run_panel(tmp_path, combiner=Recorder([json.dumps(overstated), "Not JSON."]))

    record = record_from_log(log)

    assert record.combiner.repair_error is not None

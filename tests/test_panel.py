"""The panel run end to end on mock models, offline."""

import json
from pathlib import Path

from inspect_ai.model import ModelOutput, ModelUsage, ResponseSchema, get_model
from inspect_ai.util import JSONSchema
from mock_panel import COMBINED, IDEA, Recorder, only_sample, run_panel, verdict

from skeptic.combine import combined_json_schema, statement_repair_prompt
from skeptic.critics import CRITICS, critic_system_prompt, prompts_digest, submission_prompt
from skeptic.panel import (
    PanelResult,
    ServedModel,
    Settings,
    call_usage,
    generation_config,
    parse_served_model,
)
from skeptic.verdict import verdict_json_schema


def test_panel_runs_every_critic_then_combines(tmp_path: Path) -> None:
    log, recorders = run_panel(tmp_path)

    assert log.status == "success"
    sample = only_sample(log)
    result = sample.store_as(PanelResult)
    assert [r.critic for r in result.critics] == list(CRITICS)
    assert all(r.verdict is not None and r.error is None for r in result.critics)
    assert result.combined is not None
    assert result.combined_checks is not None and not result.combined_checks.flagged
    assert sample.output.completion == "The panel's statement."
    assert all(len(recorder.calls) == 1 for recorder in recorders.values())
    assert log.eval.metadata == {"prompts_sha256": prompts_digest()}


def test_scores_count_what_the_checks_found(tmp_path: Path) -> None:
    broken = json.loads(verdict("technical", quote="words not in the idea"))
    broken["summary"] = "It stops here (e.g., "
    broken["overall"] = "weak"  # one major issue gives 'promising'
    log, _ = run_panel(tmp_path, technical=json.dumps(broken), risk="No.")

    assert only_sample(log).scores is not None
    assert only_sample(log).scores["panel_checks"].value == {  # type: ignore[index]
        "critics_answered": 3.0,
        "quotes": 3.0,
        "quotes_not_verbatim": 1.0,
        "flagged_fields": 1.0,
        "overall_mismatches": 1.0,
        "fatals_capped": 0.0,
        "combiner_flags": 1.0,  # the mock combiner names risk, which gave no review
        "statement_repaired": 0.0,
        "near_context_limit": 0.0,  # only known for Ollama models
    }


def test_fixable_fatal_is_capped_before_the_combiner_sees_it(tmp_path: Path) -> None:
    fatal = json.loads(verdict("market", severity="fatal", fixable=True))
    fatal["overall"] = "not viable"
    log, recorders = run_panel(tmp_path, market=json.dumps(fatal))

    sample = only_sample(log)
    market = sample.store_as(PanelResult).critics[0]
    assert market.verdict is not None
    assert market.verdict.issues[0].severity == "major"
    assert market.verdict.overall == "promising"
    assert market.model_overall == "not viable"
    assert market.checks is not None and market.checks.fatal_but_fixable == ["issues[0]"]
    [(messages, _)] = recorders["combiner"].calls
    assert '"severity": "fatal"' not in messages[1].text
    assert sample.scores is not None
    assert sample.scores["panel_checks"].value["fatals_capped"] == 1.0  # type: ignore[index]


def test_each_critic_sees_only_the_idea_and_its_own_prompt(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path)

    expected_schema = ResponseSchema(
        name="critic_verdict",
        json_schema=JSONSchema.model_validate(verdict_json_schema()),
        strict=True,
    )
    for critic in CRITICS:
        [(messages, config)] = recorders[critic].calls
        assert [m.role for m in messages] == ["system", "user"]
        assert messages[0].text == critic_system_prompt(critic)
        assert messages[1].text == submission_prompt(IDEA)
        assert config.response_schema == expected_schema
        assert (config.temperature, config.seed, config.max_tokens) == (0, 1, 3000)


def test_generation_settings_come_from_task_options(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path, task_args={"seed": 7, "max_tokens": 500})

    for recorder in recorders.values():
        [(_, config)] = recorder.calls
        assert (config.seed, config.max_tokens) == (7, 500)


def test_combiner_gets_every_verdict(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path)

    [(messages, config)] = recorders["combiner"].calls
    for critic in CRITICS:
        assert f"The {critic} critic's summary." in messages[1].text
    ids = [f"{critic}-1" for critic in CRITICS]
    assert config.response_schema == ResponseSchema(
        name="combined_verdict",
        json_schema=JSONSchema.model_validate(combined_json_schema(ids)),
        strict=True,
    )


def test_point_severity_is_worked_out_from_the_critics_issues(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, risk=verdict("risk", severity="minor"))

    result = only_sample(log).store_as(PanelResult)
    assert result.combined is not None
    [point] = result.combined.points
    assert point.critics == list(CRITICS)
    assert point.severity == "major"
    assert point.severity_split  # three critics said major, risk said minor


def test_unusable_critic_reply_is_recorded_and_the_panel_goes_on(tmp_path: Path) -> None:
    log, recorders = run_panel(tmp_path, risk="I cannot review this idea.")

    assert log.status == "success"
    result = only_sample(log).store_as(PanelResult)
    risk = result.critics[CRITICS.index("risk")]
    assert risk.verdict is None
    assert risk.error is not None and "no JSON object" in risk.error
    [(messages, _)] = recorders["combiner"].calls
    assert '"risk": null' in messages[1].text
    # The mock combiner ignores the schema and still cites risk-1, which the checks catch.
    assert result.combined is not None
    assert result.combined.points[0].critics == ["market", "technical", "customer"]
    assert result.combined_checks is not None
    assert result.combined_checks.unknown_issue_ids == ["risk-1"]


def test_panel_fails_when_no_critic_answers(tmp_path: Path) -> None:
    log, recorders = run_panel(tmp_path, **dict.fromkeys(CRITICS, "No."))

    assert log.status == "error"
    assert log.error is not None
    assert "no critic returned a usable verdict" in log.error.message
    assert recorders["combiner"].calls == []


def test_unusable_combiner_reply_fails_the_run_but_keeps_the_critics(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, combiner="Not JSON.")

    assert log.status == "error"
    result = only_sample(log).store_as(PanelResult)
    assert all(r.verdict is not None for r in result.critics)
    assert result.combiner_error is not None and "no JSON object" in result.combiner_error


def test_reply_cut_off_by_the_token_limit_says_so(tmp_path: Path) -> None:
    cut_off = Recorder(verdict("market")[:80], stop_reason="max_tokens")
    log, _ = run_panel(tmp_path, market=cut_off)

    market = only_sample(log).store_as(PanelResult).critics[0]
    assert market.error is not None
    assert market.error.endswith("(stop reason: max_tokens)")
    assert market.usage.stop_reason == "max_tokens"


def test_thinking_option_leaves_reasoning_settings_alone(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path, task_args={"thinking": True})

    for recorder in recorders.values():
        [(_, config)] = recorder.calls
        assert config.reasoning_effort is None
        assert config.extra_body is None


def test_thinking_is_turned_off_the_way_each_provider_understands() -> None:
    schema = {"type": "object"}
    off = Settings(thinking=False, temperature=0, seed=1, max_tokens=100)
    on = Settings(thinking=True, temperature=0, seed=1, max_tokens=100)
    ollama = get_model("ollama/any-model", memoize=False)
    other = get_model("mockllm/model", memoize=False)

    assert generation_config(ollama, "s", schema, off).extra_body == {
        "reasoning": {"effort": "none"}
    }
    assert generation_config(ollama, "s", schema, on).extra_body is None
    assert generation_config(other, "s", schema, off).reasoning_effort == "none"
    assert generation_config(other, "s", schema, on).reasoning_effort is None


def test_served_model_is_read_from_ollamas_running_models() -> None:
    running = {
        "models": [
            {"model": "other:1b", "context_length": 4096},
            {
                "model": "qwen3.5:9b-q4_K_M",
                "digest": "56671c2ab938",
                "details": {"quantization_level": "Q4_K_M", "parameter_size": "9.7B"},
                "context_length": 8192,
            },
        ]
    }

    assert parse_served_model(running, "qwen3.5:9b-q4_K_M") == ServedModel(
        digest="56671c2ab938", quantization="Q4_K_M", parameter_size="9.7B", context_length=8192
    )
    assert parse_served_model(running, "missing:1b") is None


def test_call_near_the_servers_window_is_flagged_counting_cached_tokens() -> None:
    usage = ModelUsage(
        input_tokens=1000, output_tokens=500, input_tokens_cache_read=2300, total_tokens=3800
    )
    output = ModelOutput.from_content(model="m", content="{}")
    output.usage = usage

    small_window = call_usage(output, ServedModel(context_length=4096))
    large_window = call_usage(output, ServedModel(context_length=16384))

    assert small_window.input_tokens == 3300
    assert small_window.near_context_limit
    assert not large_window.near_context_limit
    assert not call_usage(output, None).near_context_limit


def test_statement_that_calls_a_major_problem_fatal_is_repaired_once(tmp_path: Path) -> None:
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    repaired = json.dumps({"statement": "Competition is a major problem."})
    combiner = Recorder([json.dumps(overstated), repaired])
    log, _ = run_panel(tmp_path, combiner=combiner)

    sample = only_sample(log)
    result = sample.store_as(PanelResult)
    assert result.combined is not None
    assert result.combined.statement == "Competition is a major problem."
    assert result.combined.first_statement == "Competition is a fatal flaw."
    assert result.combined.statement_repaired
    assert result.combined_checks is not None and not result.combined_checks.statement_overstates
    assert sample.output.completion == "Competition is a major problem."
    first, second = combiner.calls
    assert [m.role for m in second[0]] == ["system", "user", "assistant", "user"]
    assert [m.text for m in second[0][:2]] == [m.text for m in first[0]]
    assert second[0][2].text == json.dumps(overstated)
    assert second[0][3].text == statement_repair_prompt()
    assert second[1].response_schema is not None
    assert second[1].response_schema.name == "statement_repair"
    assert result.repair_usage is not None


def test_statement_without_overstatement_is_not_repaired(tmp_path: Path) -> None:
    combiner = Recorder(COMBINED)
    log, _ = run_panel(tmp_path, combiner=combiner)

    result = only_sample(log).store_as(PanelResult)
    assert result.combined is not None and not result.combined.statement_repaired
    assert len(combiner.calls) == 1


def test_failed_repair_keeps_the_first_statement_and_the_flag(tmp_path: Path) -> None:
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    combiner = Recorder([json.dumps(overstated), "Not JSON."])
    log, _ = run_panel(tmp_path, combiner=combiner)

    assert log.status == "success"
    sample = only_sample(log)
    result = sample.store_as(PanelResult)
    assert result.combined is not None
    assert result.combined.statement == "Competition is a fatal flaw."
    assert sample.output.completion == "Competition is a fatal flaw."
    assert not result.combined.statement_repaired
    assert result.combined.first_statement is None
    assert result.repair_error is not None
    assert result.repair_usage is not None
    assert result.combined_checks is not None and result.combined_checks.statement_overstates


def test_cut_off_repair_is_not_used(tmp_path: Path) -> None:
    overstated = json.loads(COMBINED) | {"statement": "Competition is a fatal flaw."}
    fragment = json.dumps({"statement": "The 'risk' critic called the "})
    log, _ = run_panel(tmp_path, combiner=Recorder([json.dumps(overstated), fragment]))

    result = only_sample(log).store_as(PanelResult)
    assert result.combined is not None
    assert result.combined.statement == "Competition is a fatal flaw."
    assert result.repair_error is not None and "cut off" in result.repair_error

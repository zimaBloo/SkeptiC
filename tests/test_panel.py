"""The panel run end to end on mock models, offline."""

import json
from pathlib import Path

import anyio
import httpx
import openai
import pytest
from inspect_ai.model import (
    GenerateConfig,
    Model,
    ModelOutput,
    ModelUsage,
    ResponseSchema,
    get_model,
)
from inspect_ai.model._providers.openrouter import OpenRouterError
from inspect_ai.util import JSONSchema
from mock_panel import COMBINED, IDEA, Recorder, only_sample, run_panel, verdict

from skeptic import panel as panel_module
from skeptic.combine import combined_json_schema, statement_repair_prompt
from skeptic.critics import (
    CRITICS,
    CriticName,
    critic_system_prompt,
    prompts_digest,
    submission_prompt,
)
from skeptic.lineups import FRONTIER, LOCAL, ROUTES, Reasoning
from skeptic.panel import (
    CriticResult,
    PanelResult,
    ServedModel,
    Settings,
    call_usage,
    generation_config,
    parse_served_model,
    refusal_reason,
    run_together,
    skeptic_panel,
)
from skeptic.record import record_from_log
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
    assert result.critics_parallel is True
    assert log.eval.metadata == {
        "prompts_sha256": prompts_digest(),
        "settings": {
            "lineup": "local",
            "reasoning": "none",
            "temperature": 0.0,
            "seed": 1,
            "max_tokens": 3000,
        },
    }


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
        "format_retries": 1.0,
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
    # A reply cut off by the limit would be cut off again, so it is not asked for again.
    assert len(cut_off.calls) == 1 and not market.format_retried


# The way Claude Haiku broke JSON in the first smoke run: single-quoted strings.
SINGLE_QUOTED = verdict("market").replace('"summary": "', "'summary': '", 1)


def test_critic_reply_that_is_not_json_is_asked_for_once_more(tmp_path: Path) -> None:
    market = Recorder([SINGLE_QUOTED, verdict("market")])

    log, _ = run_panel(tmp_path, market=market)

    result = only_sample(log).store_as(PanelResult)
    found = result.critics[0]
    assert found.verdict is not None and found.error is None
    assert found.format_retried and found.retry_usage is not None
    assert len(market.calls) == 2
    [_, (messages, config)] = market.calls
    assert messages[-2].text == SINGLE_QUOTED
    assert "could not be read as JSON" in messages[-1].text
    assert config.response_schema is not None and config.response_schema.name == "critic_verdict"
    assert only_sample(log).scores["panel_checks"].value["format_retries"] == 1.0  # type: ignore[index]


def test_critic_reply_still_not_json_after_asking_again_gives_no_answer(tmp_path: Path) -> None:
    risk = Recorder(["I cannot review this idea.", "Still not JSON."])

    log, _ = run_panel(tmp_path, risk=risk)

    assert log.status == "success"
    found = only_sample(log).store_as(PanelResult).critics[3]
    assert found.verdict is None and found.format_retried
    assert found.error is not None and "asked again" in found.error
    assert len(risk.calls) == 2


def test_combiner_reply_that_is_not_json_is_asked_for_once_more(tmp_path: Path) -> None:
    single_quoted = COMBINED.replace('"statement": "', "'statement': '", 1)
    combiner = Recorder([single_quoted, COMBINED])

    log, _ = run_panel(tmp_path, combiner=combiner)

    assert log.status == "success"
    result = only_sample(log).store_as(PanelResult)
    assert result.combined is not None and result.combiner_format_retried
    assert result.combiner_retry_usage is not None
    assert len(combiner.calls) == 2
    assert record_from_log(log).combiner.format_retried


def test_combiner_reply_still_not_json_fails_the_run(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, combiner=Recorder(["Not JSON.", "Still not JSON."]))

    assert log.status == "error"
    result = only_sample(log).store_as(PanelResult)
    assert result.combiner_error is not None and "asked again" in result.combiner_error


def test_default_reasoning_leaves_reasoning_settings_alone(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path, task_args={"reasoning": "default"})

    for recorder in recorders.values():
        [(_, config)] = recorder.calls
        assert config.reasoning_effort is None
        assert config.extra_body is None


def test_reasoning_is_set_the_way_each_provider_understands() -> None:
    schema = {"type": "object"}

    def settings(reasoning: Reasoning) -> Settings:
        return Settings(reasoning=reasoning, temperature=0, seed=1, max_tokens=100)

    ollama = get_model("ollama/any-model", memoize=False)
    other = get_model("mockllm/model", memoize=False)

    assert generation_config(ollama, "s", schema, settings("none")).extra_body == {
        "reasoning": {"effort": "none"}
    }
    assert generation_config(ollama, "s", schema, settings("none")).reasoning_effort is None
    assert generation_config(ollama, "s", schema, settings("low")).reasoning_effort == "low"
    assert generation_config(ollama, "s", schema, settings("default")).extra_body is None
    assert generation_config(other, "s", schema, settings("none")).reasoning_effort == "none"
    assert generation_config(other, "s", schema, settings("high")).reasoning_effort == "high"
    assert generation_config(other, "s", schema, settings("default")).reasoning_effort is None


def test_frontier_models_are_pinned_to_their_labs_endpoint_at_list_price() -> None:
    opus = get_model(FRONTIER.models["combiner"], api_key="test", memoize=False)
    low = Settings(reasoning="low", temperature=0, seed=1, max_tokens=16000)

    config = generation_config(opus, "s", {"type": "object"}, low)

    assert config.extra_body == {
        "provider": {
            "only": ["anthropic"],
            "allow_fallbacks": False,
            "max_price": {"prompt": 4, "completion": 20},
        }
    }
    assert config.reasoning_effort == "low"
    assert config.max_tokens == 16000


def test_every_frontier_model_has_a_route() -> None:
    assert set(FRONTIER.models.values()) <= set(ROUTES)


def test_frontier_lineup_brings_its_models_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")

    task = skeptic_panel(idea=str(idea), lineup="frontier")

    assert task.model_roles is not None
    assert {role: str(model) for role, model in task.model_roles.items()} == dict(FRONTIER.models)
    assert task.metadata is not None
    assert task.metadata["settings"] == {
        "lineup": "frontier",
        "reasoning": "low",
        "temperature": 0.0,
        "seed": 1,
        "max_tokens": 16000,
    }


def test_task_options_override_the_lineups_settings(tmp_path: Path) -> None:
    _, recorders = run_panel(tmp_path, task_args={"reasoning": "high", "max_tokens": 500})

    for recorder in recorders.values():
        [(_, config)] = recorder.calls
        assert config.reasoning_effort == "high"
        assert config.max_tokens == 500


@pytest.mark.parametrize("task_args", [{"lineup": "cloud"}, {"reasoning": "extreme"}])
def test_unknown_lineup_or_reasoning_level_is_refused(tmp_path: Path, task_args: dict) -> None:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")

    with pytest.raises(ValueError):
        skeptic_panel(idea=str(idea), **task_args)


def test_critics_run_together_unless_one_is_a_local_model() -> None:
    hosted = get_model("mockllm/model", memoize=False)
    local = get_model(LOCAL.models["risk"], memoize=False)

    assert run_together([hosted, hosted])
    assert not run_together([hosted, local])


def test_local_critics_run_one_after_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_together(*tasks: object) -> None:
        raise AssertionError("local critics must not run at the same time")

    monkeypatch.setattr("skeptic.panel.run_together", lambda critics: False)
    monkeypatch.setattr("skeptic.panel.collect", not_together)

    log, _ = run_panel(tmp_path)

    result = only_sample(log).store_as(PanelResult)
    assert result.critics_parallel is False
    assert [r.critic for r in result.critics] == list(CRITICS)


class Refused(Recorder):
    """A mock model whose provider refuses the call, as OpenRouter does without credit."""

    def __call__(self, input, tools, tool_choice, config):  # type: ignore[no-untyped-def]
        self.calls.append((input, config))
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        raise openai.APIStatusError(
            "Error code: 402 - insufficient credits",
            response=httpx.Response(402, request=request),  # type: ignore[arg-type]
            body=None,
        )


def test_refused_critic_call_is_recorded_and_the_others_go_on(tmp_path: Path) -> None:
    log, recorders = run_panel(tmp_path, risk=Refused("unused"))

    assert log.status == "success"
    result = only_sample(log).store_as(PanelResult)
    risk = result.critics[3]
    assert risk.critic == "risk" and risk.verdict is None
    assert risk.error is not None and risk.error.startswith("the model call failed:")
    assert "402" in risk.error
    assert all(r.verdict is not None for r in result.critics[:3])
    [(messages, _)] = recorders["combiner"].calls
    assert '"risk": null' in messages[1].text


def openrouter_that_refuses(error: type[openai.APIStatusError], status: int) -> Model:
    """A real OpenRouter model whose HTTP call fails with the given status."""
    model = get_model(FRONTIER.models["risk"], api_key="test", memoize=False)

    async def refuse(request: dict, config: GenerateConfig) -> None:
        request_ = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        raise error(
            f"Error code: {status} - refused",
            response=httpx.Response(status, request=request_),  # type: ignore[arg-type]
            body={"error": {"message": "refused", "code": status}},
        )

    model.api._generate_completion = refuse  # type: ignore[attr-defined,method-assign]
    return model


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (openai.BadRequestError, 400),
        (openai.PermissionDeniedError, 403),  # OpenRouter's moderation
        (openai.UnprocessableEntityError, 422),
        (openai.APIStatusError, 402),
        (openai.NotFoundError, 404),
    ],
)
def test_any_refused_call_leaves_the_other_critics_going(
    tmp_path: Path, error: type[openai.APIStatusError], status: int
) -> None:
    log, _ = run_panel(tmp_path, risk=openrouter_that_refuses(error, status))

    assert log.status == "success"
    result = only_sample(log).store_as(PanelResult)
    risk = result.critics[3]
    assert risk.verdict is None and risk.error is not None
    assert risk.error.startswith("the model call failed:") and str(status) in risk.error
    assert "messages" not in risk.error  # the provider's words, not Inspect's request dump
    assert all(r.verdict is not None for r in result.critics[:3])


def test_refusal_without_details_still_says_why() -> None:
    bare = OpenRouterError({"code": 402, "message": "Insufficient credits"})

    assert refusal_reason(bare) == "Error 402 - Insufficient credits"


def test_frontier_models_cannot_be_asked_not_to_reason(tmp_path: Path) -> None:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")

    with pytest.raises(ValueError, match="always reason"):
        skeptic_panel(idea=str(idea), lineup="frontier", reasoning="none")


def test_panel_fails_when_every_critic_call_is_refused(tmp_path: Path) -> None:
    refused: dict[CriticName, Recorder] = {critic: Refused("unused") for critic in CRITICS}
    log, _ = run_panel(tmp_path, **refused)

    assert log.status == "error"
    assert log.samples is not None and log.samples[0].error is not None
    assert "402" in log.samples[0].error.message


def test_openrouter_request_carries_the_route_and_the_reasoning_level() -> None:
    opus = get_model(FRONTIER.models["combiner"], api_key="test", memoize=False)
    low = Settings(reasoning="low", temperature=0, seed=1, max_tokens=16000)
    config = generation_config(opus, "s", {"type": "object"}, low)

    params = opus.api.completion_params(config, False)  # type: ignore[attr-defined]

    route = ROUTES[FRONTIER.models["combiner"]]
    assert params["extra_body"]["provider"] == route.provider_options()
    assert params["extra_body"]["reasoning"] == {"effort": "low"}
    assert "reasoning_effort" not in params


def test_critics_that_finish_out_of_order_are_kept_in_the_panels_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = panel_module.run_critic

    async def first_is_slowest(critic: CriticName, idea: str, settings: Settings) -> CriticResult:
        await anyio.sleep(0.05 * (len(CRITICS) - CRITICS.index(critic)))
        return await real(critic, idea, settings)

    monkeypatch.setattr("skeptic.panel.run_critic", first_is_slowest)
    progress = tmp_path / "progress.jsonl"

    log, _ = run_panel(tmp_path, task_args={"progress_file": str(progress)})

    lines = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
    finished = [e["critic"] for e in lines if e["event"] == "critic_finished"]
    assert finished == list(reversed(CRITICS))
    result = only_sample(log).store_as(PanelResult)
    assert [r.critic for r in result.critics] == list(CRITICS)


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


def test_idea_id_option_names_the_sample(tmp_path: Path) -> None:
    log, _ = run_panel(tmp_path, task_args={"idea_id": "shelfsense"})

    assert only_sample(log).id == "shelfsense"


def test_usage_of_several_calls_adds_up_and_keeps_unknown_costs_unknown() -> None:
    from skeptic.panel import CallUsage, total_usage

    first = CallUsage(input_tokens=100, output_tokens=50, cost=0.01)
    second = CallUsage(input_tokens=40, output_tokens=10, cost=None)
    total = total_usage(first, None, second)

    assert total is not None
    assert (total.input_tokens, total.output_tokens, total.cost) == (140, 60, 0.01)
    assert total_usage(None, None) is None
    assert total_usage(CallUsage(input_tokens=5)) == CallUsage(input_tokens=5)

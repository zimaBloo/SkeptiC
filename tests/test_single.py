"""The single-judge baselines, on a mock model, offline."""

import json
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.log import EvalLog
from inspect_ai.model import get_model
from mock_panel import IDEA, Recorder, only_sample

from skeptic.critics import TITLES
from skeptic.single import SingleResult, checklist_prompt, single_judge

CHECKLIST_REPLY = json.dumps(
    {
        "issues": [
            {
                "quote": "There are no direct competitors",
                "issue": "Competitors exist.",
                "why_it_matters": "It weakens the plan.",
                "how_to_improve": "Name them.",
                "fixable_within_idea": True,
                "severity": "major",
            },
            {
                "quote": "words that are not in the idea",
                "issue": "A made-up quote.",
                "why_it_matters": "It is wrong.",
                "how_to_improve": "Quote exactly.",
                "fixable_within_idea": True,
                "severity": "minor",
            },
        ],
        "statement": "The single judge's statement.",
        "overall": "promising",
    }
)


def run_single(tmp_path: Path, style: str, reply: Recorder) -> EvalLog:
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA, encoding="utf-8")
    [log] = inspect_eval(
        single_judge(idea=str(idea), style=style, judge="mockllm/model"),
        model_roles={"judge": get_model("mockllm/model", custom_outputs=reply, memoize=False)},
        display="none",
        log_dir=str(tmp_path / "logs"),
    )
    return log


def test_plain_judge_gets_one_question_and_answers_freely(tmp_path: Path) -> None:
    judge = Recorder("It has no competitors analysis.")

    log = run_single(tmp_path, "plain", judge)

    assert log.status == "success"
    [(messages, config)] = judge.calls
    assert len(messages) == 1 and IDEA in messages[0].text
    assert "weak or risky" in messages[0].text
    assert config.response_schema is None
    assert only_sample(log).output.completion == "It has no competitors analysis."


def test_checklist_judge_uses_the_critics_rules_and_checks_its_quotes(tmp_path: Path) -> None:
    judge = Recorder(CHECKLIST_REPLY)

    log = run_single(tmp_path, "checklist", judge)

    assert log.status == "success"
    [(messages, config)] = judge.calls
    assert messages[0].text == checklist_prompt()
    assert config.response_schema is not None and config.response_schema.name == "single_verdict"
    result = only_sample(log).store_as(SingleResult)
    assert result.verdict is not None and len(result.verdict.issues) == 2
    assert result.checks is not None
    assert result.checks.quotes_not_in_idea == ["words that are not in the idea"]
    assert only_sample(log).output.completion == "The single judge's statement."


def test_checklist_judge_is_asked_once_more_for_a_reply_that_is_not_json(tmp_path: Path) -> None:
    judge = Recorder(["Not JSON.", CHECKLIST_REPLY])

    log = run_single(tmp_path, "checklist", judge)

    result = only_sample(log).store_as(SingleResult)
    assert result.format_retried and result.verdict is not None
    assert len(judge.calls) == 2


def test_checklist_prompt_covers_every_angle_without_handing_any_off() -> None:
    prompt = checklist_prompt()

    for title in TITLES.values():
        assert title.capitalize() in prompt
    assert "other critics" not in prompt
    assert "$" not in prompt

"""One model judging an idea alone: the baselines the panel is compared with.

- plain: the question a person would type into a chatbot, with no rules and no
  answer format.
- checklist: one call with the panel's own checklist. All four angles go into
  one prompt, with the critics' rules, severity scale and rating, so that a
  difference from the panel comes from splitting the work across critics and
  model families, not from a better prompt.

Run it with:

    uv run inspect eval skeptic/single_judge -T idea=ideas/shelfsense.md -T style=plain

The judge is the model role "judge"; by default the panel's own combiner
model (Claude Opus 5.5 through OpenRouter), the strongest model in the panel.
"""

import re
from pathlib import Path
from string import Template
from typing import Literal, cast

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    ModelOutput,
    get_model,
)
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import StoreModel, store_as
from pydantic import BaseModel, ConfigDict, Field

from skeptic.checks import VerdictChecks, check_verdict
from skeptic.critics import CRITICS, TITLES, format_repair_prompt, load_prompt, prompts_digest
from skeptic.lineups import FRONTIER, LOCAL, Reasoning, check_reasoning
from skeptic.panel import CallUsage, Settings, call_usage, generation_config
from skeptic.verdict import (
    CriticVerdict,
    Issue,
    Overall,
    VerdictParseError,
    json_schema_for,
    parse_reply,
)

Style = Literal["plain", "checklist"]
STYLES: tuple[Style, ...] = ("plain", "checklist")
DEFAULT_JUDGE = FRONTIER.models["combiner"]


class SingleVerdict(BaseModel):
    """What the checklist judge returns: issues from all four angles, a statement, a rating."""

    model_config = ConfigDict(extra="forbid")

    issues: list[Issue] = Field(description="The most important problems, most serious first.")
    statement: str = Field(
        description="The verdict for the author: the most serious problems first, then what "
        "to change, in one or two short paragraphs."
    )
    overall: Overall


class SingleResult(StoreModel):
    """What one judge produced for one idea, kept in the sample's store."""

    style: Style | None = None
    model: str | None = None
    verdict: SingleVerdict | None = Field(
        default=None, description="The checklist judge's verdict; none for the plain judge."
    )
    checks: VerdictChecks | None = None
    format_retried: bool = False
    usage: CallUsage = Field(default_factory=CallUsage)
    retry_usage: CallUsage | None = None
    error: str | None = None


def checklist_prompt() -> str:
    """The checklist judge's system prompt: the four critics' focuses in one prompt."""
    focuses = []
    for critic in CRITICS:
        focus = load_prompt(f"roles/{critic}.md").strip()
        # The last paragraph of each focus hands topics to the other critics; a
        # judge working alone covers them all.
        focus = re.split(r"\n\s*\nLeave these to the other critics", focus)[0].strip()
        focuses.append(f"### {TITLES[critic].capitalize()}\n\n{focus}")
    return Template(load_prompt("single_checklist.md")).substitute(focus="\n\n".join(focuses))


def plain_prompt(idea: str) -> str:
    return Template(load_prompt("single_plain.md")).substitute(idea=idea.strip())


@task
def single_judge(
    idea: str,
    idea_id: str | None = None,
    style: str = "checklist",
    judge: str = DEFAULT_JUDGE,
    reasoning: str = "low",
    max_tokens: int = 16000,
    temperature: float = 0.0,
    seed: int = 1,
) -> Task:
    """Have one model judge an idea alone.

    Args:
        idea: Path to a text or Markdown file with the idea.
        idea_id: The idea's id in the log. Defaults to the file's name.
        style: "plain" (one question, free text) or "checklist" (the panel's
            checklist in one prompt, with the critics' answer format).
        judge: The model that judges.
        reasoning: How much the model reasons (see skeptic.lineups).
        max_tokens: Most tokens the reply may use, reasoning included.
        temperature: Sampling temperature.
        seed: Sampling seed.
    """
    if style not in STYLES:
        raise ValueError(f"style must be one of {', '.join(STYLES)}, not {style!r}")
    check_reasoning(FRONTIER if judge.startswith("openrouter/") else LOCAL, reasoning)
    path = Path(idea)
    settings = Settings(
        reasoning=cast(Reasoning, reasoning),
        temperature=temperature,
        seed=seed,
        max_tokens=max_tokens,
    )
    return Task(
        dataset=[
            Sample(
                id=idea_id or path.stem,
                input=path.read_text(encoding="utf-8"),
                metadata={"idea_file": path.as_posix()},
            )
        ],
        solver=judge_alone(cast(Style, style), settings),
        scorer=single_checks(),
        model="none",
        model_roles={"judge": judge},
        metadata={
            "prompts_sha256": prompts_digest(),
            "settings": {"style": style, **vars(settings)},
        },
    )


@solver
def judge_alone(style: Style, settings: Settings) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        idea = state.input_text
        model = get_model(role="judge", required=True)
        result = store_as(SingleResult)
        result.style = style
        result.model = str(model)
        if style == "plain":
            config = generation_config(model, "unused", {"type": "object"}, settings)
            config = config.model_copy(update={"response_schema": None})
            output = await model.generate(
                [ChatMessageUser(content=plain_prompt(idea))], config=config
            )
            result.usage = call_usage(output, None)
            state.output = output
            return state

        config = generation_config(
            model, "single_verdict", json_schema_for(SingleVerdict), settings
        )
        messages: list[ChatMessage] = [
            ChatMessageSystem(content=checklist_prompt()),
            ChatMessageUser(
                content=Template(load_prompt("submission.md")).substitute(idea=idea.strip())
            ),
        ]
        output = await model.generate(messages, config=config)
        result.usage = call_usage(output, None)
        try:
            verdict, _ = parse_reply(output.completion, SingleVerdict)
        except VerdictParseError as error:
            if output.stop_reason != "stop":
                result.error = f"{error} (stop reason: {output.stop_reason})"
                state.output = output
                return state
            result.format_retried = True
            output = await model.generate(
                [
                    *messages,
                    ChatMessageAssistant(content=output.completion),
                    ChatMessageUser(content=format_repair_prompt(str(error))),
                ],
                config=config,
            )
            result.retry_usage = call_usage(output, None)
            try:
                verdict, _ = parse_reply(output.completion, SingleVerdict)
            except VerdictParseError as again:
                result.error = f"{error}; asked again: {again}"
                state.output = output
                return state
        result.verdict = verdict
        result.checks = check_verdict(
            CriticVerdict(
                issues=verdict.issues, summary=verdict.statement, overall=verdict.overall
            ),
            idea,
        )
        state.output = ModelOutput.from_content(model=str(model), content=verdict.statement)
        return state

    return solve


@scorer(metrics={"answered": [mean()], "quotes": [mean()], "quotes_not_verbatim": [mean()]})
def single_checks() -> Scorer:
    """Count what the checks found. For the plain judge only 'answered' applies."""

    async def score(state: TaskState, target: Target) -> Score:
        result = store_as(SingleResult)
        answered = result.error is None and bool(state.output.completion.strip())
        verdict = result.verdict
        return Score(
            value={
                "answered": float(answered),
                "quotes": float(len(verdict.issues)) if verdict else 0.0,
                "quotes_not_verbatim": float(len(result.checks.quotes_not_in_idea))
                if result.checks
                else 0.0,
            },
            explanation=result.error,
        )

    return score

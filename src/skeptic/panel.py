"""The critic panel as an Inspect task.

Each critic gets the idea and its own system prompt, and nothing else: no
other critic's answer and no score to anchor on. Hosted critics run at the same
time; local ones run one after another, since one Ollama server holds one model
at a time. Two of the prompt's severity rules are then applied in code (see
verdict.apply_severity_rules), and the combining step merges the verdicts and
writes the panel's statement. Inspect logs every model call, so each verdict can
be traced back to the exact prompt and reply.

Run it with:

    uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md
    uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md -T lineup=frontier

Each critic and the combiner is a model role, so any of them can be swapped on
the command line, e.g. --model-role market=ollama/gemma4:12b-it-qat. The
lineups are in skeptic.lineups.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx
import openai
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.event import ModelEvent
from inspect_ai.log import transcript
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    Model,
    ModelOutput,
    ResponseSchema,
    get_model,
)
from inspect_ai.model._model import ModelGenerateError
from inspect_ai.model._providers.openrouter import OpenRouterError
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import JSONSchema, StoreModel, collect, store_as
from pydantic import BaseModel, Field

from skeptic.checks import (
    CombinedChecks,
    VerdictChecks,
    check_combined,
    check_verdict,
    is_complete_sentence,
)
from skeptic.combine import (
    CombinedVerdict,
    combined_json_schema,
    combiner_system_prompt,
    issue_ids,
    parse_combined,
    parse_repair,
    repair_json_schema,
    resolve_combined,
    reviews_prompt,
    statement_repair_prompt,
)
from skeptic.critics import (
    CRITICS,
    CriticName,
    critic_system_prompt,
    format_repair_prompt,
    prompts_digest,
    submission_prompt,
)
from skeptic.lineups import (
    ROLES,
    ROUTES,
    Reasoning,
    check_reasoning,
    get_lineup,
)
from skeptic.progress import (
    CombinerFinished,
    CombinerStarted,
    CriticFinished,
    CriticStarted,
    ProgressWriter,
    RepairStarted,
    RunFinished,
    RunStarted,
    Usage,
)
from skeptic.verdict import (
    CriticVerdict,
    Overall,
    VerdictParseError,
    apply_severity_rules,
    parse_verdict,
    verdict_json_schema,
)

# The shape of what the panel stores for a run. Raise it when that shape changes,
# so that older logs are recognised as older.
RECORD_FORMAT = 2

# A call that uses this share of the server's context window may have had its
# prompt cut: Ollama shortens a prompt that does not fit, without an error.
CONTEXT_WARNING_SHARE = 0.9


@dataclass(frozen=True)
class Settings:
    """Generation settings shared by every call in a run."""

    reasoning: Reasoning
    temperature: float
    seed: int
    max_tokens: int


class ServedModel(BaseModel):
    """What the Ollama server reports about a model it has just run."""

    digest: str | None = None
    quantization: str | None = None
    parameter_size: str | None = None
    context_length: int | None = None


class CallUsage(BaseModel):
    """Token use and stop reason for one model call."""

    input_tokens: int | None = Field(
        default=None, description="Prompt tokens, including any the server read from cache."
    )
    output_tokens: int | None = Field(default=None, description="Reasoning tokens included.")
    reasoning_tokens: int | None = None
    cost: float | None = Field(
        default=None, description="Dollars, as the provider billed them (OpenRouter only)."
    )
    stop_reason: str | None = None
    near_context_limit: bool = Field(
        default=False,
        description="The call used most of the server's context window, so the prompt may "
        "have been cut. Only known for models whose server reports its window (Ollama).",
    )


class CriticResult(BaseModel):
    """One critic's outcome: a verdict with its checks, or the reason there is none."""

    critic: CriticName
    model: str
    served: ServedModel | None = None
    verdict: CriticVerdict | None = Field(
        default=None,
        description="The verdict after the severity rules: fixable fatal issues capped at "
        "major, and the overall rating worked out from the severities.",
    )
    model_overall: Overall | None = Field(
        default=None, description="The overall rating the critic itself gave."
    )
    capped: list[int] = Field(
        default_factory=list,
        description="Positions of issues the panel lowered from fatal to major.",
    )
    extracted: bool = False
    format_retried: bool = Field(
        default=False,
        description="The first reply was not valid JSON, so the critic was asked once more.",
    )
    checks: VerdictChecks | None = None
    usage: CallUsage = Field(default_factory=CallUsage)
    retry_usage: CallUsage | None = None
    error: str | None = None


class PanelResult(StoreModel):
    """Everything the panel produced for one idea, kept in the sample's store."""

    record_format: int | None = Field(
        default=None, description="RECORD_FORMAT of the code that ran; missing in older logs."
    )
    ollama_version: str | None = None
    critics_parallel: bool | None = Field(
        default=None, description="The critics ran at the same time; missing in older logs."
    )
    critics: list[CriticResult] = Field(default_factory=list)
    combiner_model: str | None = None
    combiner_served: ServedModel | None = None
    combiner_usage: CallUsage | None = None
    combiner_format_retried: bool = Field(
        default=False,
        description="The combiner's first reply was not valid JSON, so it was asked once more.",
    )
    combiner_retry_usage: CallUsage | None = None
    combiner_error: str | None = None
    repair_usage: CallUsage | None = None
    repair_error: str | None = None
    combined: CombinedVerdict | None = None
    combined_extracted: bool = False
    combined_checks: CombinedChecks | None = None


@task
def skeptic_panel(
    idea: str,
    idea_id: str | None = None,
    lineup: str = "local",
    reasoning: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    seed: int = 1,
    progress_file: str | None = None,
) -> Task:
    """Run the critic panel on one idea.

    Args:
        idea: Path to a text or Markdown file with the idea.
        idea_id: The idea's id in the log and the record. Defaults to the file's
            name without its extension.
        lineup: The models to run on: "local" or "frontier" (see skeptic.lineups).
        reasoning: How much the models reason: "none", "low", "medium", "high",
            or "default" (send no setting). Defaults to the lineup's level.
        max_tokens: Most tokens any one reply may use, reasoning included.
            Defaults to the lineup's limit.
        temperature: Sampling temperature for every call.
        seed: Sampling seed for every call.
        progress_file: File to append progress events to, one JSON object per
            line (see skeptic.progress).

    Set the generation settings with these task options (-T seed=2), not with
    Inspect's own --temperature or --seed, which the panel's calls override.
    """
    path = Path(idea)
    chosen = get_lineup(lineup)
    level = reasoning or chosen.reasoning
    check_reasoning(chosen, level)
    settings = Settings(
        reasoning=cast(Reasoning, level),
        temperature=temperature,
        seed=seed,
        max_tokens=max_tokens or chosen.max_tokens,
    )
    return Task(
        dataset=[
            Sample(
                id=idea_id or path.stem,
                input=path.read_text(encoding="utf-8"),
                metadata={"idea_file": path.as_posix()},
            )
        ],
        solver=panel(**vars(settings), progress_file=progress_file),
        scorer=panel_checks(),
        # The solver only uses the model roles below, never the main model.
        model="none",
        model_roles=dict(chosen.models),
        # The settings as the run used them, after the lineup's defaults.
        metadata={
            "prompts_sha256": prompts_digest(),
            "settings": {"lineup": chosen.name, **vars(settings)},
        },
    )


@solver
def panel(
    reasoning: Reasoning = "none",
    temperature: float = 0.0,
    seed: int = 1,
    max_tokens: int = 3000,
    progress_file: str | None = None,
) -> Solver:
    """Run the critics independently, then combine their verdicts."""
    settings = Settings(reasoning, temperature, seed, max_tokens)

    async def review(state: TaskState, progress: ProgressWriter) -> TaskState:
        idea = state.input_text
        result = store_as(PanelResult)
        result.record_format = RECORD_FORMAT
        models = {role: str(get_model(role=role, required=True)) for role in ROLES}
        parallel = run_together([get_model(role=critic, required=True) for critic in CRITICS])
        result.critics_parallel = parallel
        progress.write(RunStarted(critics=list(CRITICS), models=models, parallel=parallel))
        result.ollama_version = await ollama_version()

        async def review_by(critic: CriticName) -> None:
            progress.write(CriticStarted(critic=critic, model=models[critic]))
            found = await run_critic(critic, idea, settings)
            result.critics = [*result.critics, found]
            progress.write(
                CriticFinished(
                    critic=critic,
                    ok=found.verdict is not None,
                    issues=len(found.verdict.issues) if found.verdict else None,
                    overall=found.verdict.overall if found.verdict else None,
                    top_issue=found.verdict.issues[0].issue
                    if found.verdict and found.verdict.issues
                    else None,
                    error=found.error,
                    usage=progress_usage(total_usage(found.usage, found.retry_usage)),
                )
            )

        if parallel:
            await collect(*(review_by(critic) for critic in CRITICS))
        else:
            for critic in CRITICS:
                await review_by(critic)
        # Critics that ran at the same time finish in any order; the stored list
        # and everything built from it keep the panel's own order.
        result.critics = sorted(result.critics, key=lambda r: CRITICS.index(r.critic))

        verdicts: dict[CriticName, CriticVerdict | None] = {
            r.critic: r.verdict for r in result.critics
        }
        capped: dict[CriticName, list[int]] = {r.critic: r.capped for r in result.critics}
        if not any(verdicts.values()):
            errors = "; ".join(f"{r.critic}: {r.error}" for r in result.critics)
            raise RuntimeError(f"no critic returned a usable verdict ({errors})")

        model = get_model(role="combiner", required=True)
        result.combiner_model = str(model)
        progress.write(CombinerStarted(model=str(model)))
        messages: list[ChatMessage] = [
            ChatMessageSystem(content=combiner_system_prompt()),
            ChatMessageUser(content=reviews_prompt(idea, verdicts, capped)),
        ]
        config = generation_config(
            model, "combined_verdict", combined_json_schema(issue_ids(verdicts)), settings
        )
        output = await model.generate(messages, config=config)
        result.combiner_served = await served_model(model)
        result.combiner_usage = call_usage(
            output, result.combiner_served, last_call_cost("combiner")
        )

        def combiner_usage() -> Usage | None:
            return progress_usage(
                total_usage(result.combiner_usage, result.combiner_retry_usage, result.repair_usage)
            )

        def combiner_failed(failure: str, cause: Exception) -> NoReturn:
            result.combiner_error = failure
            progress.write(
                CombinerFinished(ok=False, points=None, error=failure, usage=combiner_usage())
            )
            raise VerdictParseError(failure) from cause

        try:
            reply, extracted = parse_combined(output.completion)
        except VerdictParseError as error:
            failure = with_stop_reason(str(error), output)
            if output.stop_reason != "stop":
                # Cut off by the token limit: asking again would be cut off too.
                combiner_failed(failure, error)
            # Not every provider holds a model to the schema; ask once more.
            result.combiner_format_retried = True
            messages = [
                *messages,
                ChatMessageAssistant(content=output.completion),
                ChatMessageUser(content=format_repair_prompt(str(error))),
            ]
            output = await model.generate(messages, config=config)
            result.combiner_retry_usage = call_usage(
                output, result.combiner_served, last_call_cost("combiner")
            )
            try:
                reply, extracted = parse_combined(output.completion)
            except VerdictParseError as again:
                combiner_failed(
                    f"{failure}; asked again: {with_stop_reason(str(again), output)}", again
                )
        combined = resolve_combined(reply, verdicts, capped)
        result.combined = combined
        result.combined_extracted = extracted
        result.combined_checks = check_combined(combined, verdicts)

        if result.combined_checks.statement_overstates:
            # The statement calls a major problem fatal, usually because a critic's own
            # words still say 'fatal' after the panel lowered the issue. Ask once for a
            # rewrite; keep the first statement either way.
            progress.write(RepairStarted())
            repair = await model.generate(
                [
                    *messages,
                    ChatMessageAssistant(content=output.completion),
                    ChatMessageUser(content=statement_repair_prompt()),
                ],
                config=generation_config(model, "statement_repair", repair_json_schema(), settings),
            )
            result.repair_usage = call_usage(
                repair, result.combiner_served, last_call_cost("combiner")
            )
            try:
                repaired = parse_repair(repair.completion)
                if not is_complete_sentence(repaired.statement):
                    raise VerdictParseError("the rewritten statement is empty or cut off")
            except VerdictParseError as error:
                result.repair_error = with_stop_reason(str(error), repair)
            else:
                combined = combined.model_copy(
                    update={
                        "statement": repaired.statement,
                        "statement_repaired": True,
                        "first_statement": combined.statement,
                    }
                )
                result.combined = combined
                result.combined_checks = check_combined(combined, verdicts)

        progress.write(
            CombinerFinished(
                ok=True, points=len(combined.points), error=None, usage=combiner_usage()
            )
        )
        state.output = ModelOutput.from_content(model=str(model), content=combined.statement)
        return state

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        progress = ProgressWriter(progress_file)
        try:
            state = await review(state, progress)
        except Exception as error:
            progress.write(RunFinished(ok=False, error=str(error) or repr(error)))
            raise
        progress.write(RunFinished(ok=True, error=None))
        return state

    return solve


async def run_critic(critic: CriticName, idea: str, settings: Settings) -> CriticResult:
    """Ask one critic for its verdict.

    A reply that cannot be parsed, and a call the provider refuses or fails, are
    recorded as the critic's error, not raised, so the other critics go on.
    """
    model = get_model(role=critic, required=True)
    messages: list[ChatMessage] = [
        ChatMessageSystem(content=critic_system_prompt(critic)),
        ChatMessageUser(content=submission_prompt(idea)),
    ]
    config = generation_config(model, "critic_verdict", verdict_json_schema(), settings)
    try:
        output = await model.generate(messages, config=config)
    except REFUSALS as error:
        # Inspect has already retried what can be retried (rate limits, server
        # errors). What is left is a refusal: no credit, no endpoint within the
        # price cap, a request the provider rejects or its moderation blocks.
        failed = f"the model call failed: {refusal_reason(error)}"
        return CriticResult(critic=critic, model=str(model), error=failed)
    served = await served_model(model)
    result = CriticResult(
        critic=critic,
        model=str(model),
        served=served,
        usage=call_usage(output, served, last_call_cost(critic)),
    )
    try:
        parsed = parse_verdict(output.completion)
    except VerdictParseError as error:
        result.error = with_stop_reason(str(error), output)
        if output.stop_reason != "stop":
            # Cut off by the token limit: asking again would be cut off too.
            return result
        # Not every provider holds a model to the schema; ask once more.
        result.format_retried = True
        try:
            retry = await model.generate(
                [
                    *messages,
                    ChatMessageAssistant(content=output.completion),
                    ChatMessageUser(content=format_repair_prompt(str(error))),
                ],
                config=config,
            )
        except REFUSALS as refused:
            result.error = f"{error}; asking again failed: {refusal_reason(refused)}"
            return result
        result.retry_usage = call_usage(retry, served, last_call_cost(critic))
        try:
            parsed = parse_verdict(retry.completion)
        except VerdictParseError as again:
            result.error = f"{error}; asked again: {with_stop_reason(str(again), retry)}"
            return result
        result.error = None
    # Checks look at the verdict as the critic wrote it; the panel then applies the
    # severity rules, so the combiner and the scores see the adjusted verdict.
    result.checks = check_verdict(parsed.verdict, idea)
    result.verdict, result.capped = apply_severity_rules(parsed.verdict)
    result.model_overall = parsed.verdict.overall
    result.extracted = parsed.extracted
    return result


def generation_config(
    model: Model, schema_name: str, schema: dict[str, Any], settings: Settings
) -> GenerateConfig:
    """Settings for every panel call, with the reply forced into the given schema."""
    config = GenerateConfig(
        temperature=settings.temperature,
        seed=settings.seed,
        max_tokens=settings.max_tokens,
        response_schema=ResponseSchema(
            name=schema_name, json_schema=JSONSchema.model_validate(schema), strict=True
        ),
    )
    route = ROUTES.get(str(model))
    if route is not None:
        config = config.merge(GenerateConfig(extra_body={"provider": route.provider_options()}))
    if settings.reasoning == "default":
        return config
    if settings.reasoning == "none" and is_ollama(model):
        # Inspect's Ollama provider drops reasoning_effort="none" (it only passes
        # low, medium and high), and Ollama models that can think do so by default.
        # Ollama's own field turns thinking off.
        extra = {**(config.extra_body or {}), "reasoning": {"effort": "none"}}
        return config.merge(GenerateConfig(extra_body=extra))
    return config.merge(GenerateConfig(reasoning_effort=settings.reasoning))


# What a provider's refusal of a call looks like once Inspect has given up on it.
REFUSALS = (openai.APIError, OpenRouterError, ModelGenerateError)


def refusal_reason(error: Exception) -> str:
    """The provider's own words for a refused call, without Inspect's request dump."""
    provider_message = getattr(error, "provider_message", None)
    if provider_message:
        status = getattr(error, "status_code", None)
        return f"Error code: {status} - {provider_message}" if status else str(provider_message)
    # Inspect's OpenRouterError prints as "" when the error has no metadata.
    return str(error) or str(getattr(error, "message", "")) or repr(error)


def run_together(critics: list[Model]) -> bool:
    """Whether the critics run at the same time: yes, unless one is a local model.

    Local critics run one after another. The Ollama server holds one model at a
    time and would queue them anyway, and each call's timing then measures that
    call alone. Hosted critics do not wait for each other.
    """
    return not any(is_ollama(model) for model in critics)


def is_ollama(model: Model) -> bool:
    return str(model).startswith("ollama/")


def call_usage(
    output: ModelOutput, served: ServedModel | None, cost: float | None = None
) -> CallUsage:
    usage = output.usage
    if usage is None:
        return CallUsage(stop_reason=output.stop_reason, cost=cost)
    # Inspect counts cached prompt tokens apart from input_tokens.
    prompt = (
        usage.input_tokens
        + (usage.input_tokens_cache_read or 0)
        + (usage.input_tokens_cache_write or 0)
    )
    window = served.context_length if served else None
    return CallUsage(
        input_tokens=prompt,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost=cost if cost is not None else usage.total_cost,
        stop_reason=output.stop_reason,
        near_context_limit=window is not None
        and prompt + usage.output_tokens >= CONTEXT_WARNING_SHARE * window,
    )


def last_call_cost(role: str) -> float | None:
    """What the provider billed for the role's latest call, if it says so.

    OpenRouter puts the cost of each call in its reply's usage; Inspect keeps the
    raw reply in the transcript but not on the output.
    """
    for event in reversed(transcript().events):
        if isinstance(event, ModelEvent) and event.role == role:
            response = event.call.response if event.call else None
            usage = response.get("usage") if isinstance(response, dict) else None
            cost = usage.get("cost") if isinstance(usage, dict) else None
            return float(cost) if isinstance(cost, int | float) else None
    return None


def total_usage(*usages: CallUsage | None) -> CallUsage | None:
    """The sum of several calls' usage; None when there was no call."""
    present = [u for u in usages if u is not None]
    if not present:
        return None

    def add(values: list[int | None] | list[float | None]) -> Any:
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    return CallUsage(
        input_tokens=add([u.input_tokens for u in present]),
        output_tokens=add([u.output_tokens for u in present]),
        reasoning_tokens=add([u.reasoning_tokens for u in present]),
        cost=add([u.cost for u in present]),
        stop_reason=present[-1].stop_reason,
        near_context_limit=any(u.near_context_limit for u in present),
    )


def progress_usage(usage: CallUsage | None) -> Usage | None:
    if usage is None:
        return None
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cost=usage.cost,
    )


async def served_model(model: Model) -> ServedModel | None:
    """Ask the Ollama server about a model it has just run. None for other providers."""
    if not is_ollama(model):
        return None
    running = await _ollama_get(model.api.base_url, "/api/ps")
    return parse_served_model(running, model.api.model_name) if running else None


async def ollama_version() -> str | None:
    """The Ollama server's version, if the panel uses Ollama models."""
    for role in ROLES:
        model = get_model(role=role, required=True)
        if is_ollama(model):
            reply = await _ollama_get(model.api.base_url, "/api/version")
            return str(reply["version"]) if reply and "version" in reply else None
    return None


def parse_served_model(running: dict[str, Any], name: str) -> ServedModel | None:
    """Pick one model out of Ollama's /api/ps reply."""
    for entry in running.get("models", []):
        if name in (entry.get("model"), entry.get("name")):
            details = entry.get("details") or {}
            return ServedModel(
                digest=entry.get("digest"),
                quantization=details.get("quantization_level"),
                parameter_size=details.get("parameter_size"),
                context_length=entry.get("context_length"),
            )
    return None


async def _ollama_get(base_url: str | None, path: str) -> dict[str, Any] | None:
    # Ollama's own API sits next to its OpenAI-compatible one, at the same host.
    root = (base_url or "http://localhost:11434/v1").rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(root + path)
            response.raise_for_status()
            reply = response.json()
    except httpx.HTTPError, ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def with_stop_reason(message: str, output: ModelOutput) -> str:
    if output.stop_reason == "stop":
        return message
    return f"{message} (stop reason: {output.stop_reason})"


@scorer(
    metrics={
        "critics_answered": [mean()],
        "quotes": [mean()],
        "quotes_not_verbatim": [mean()],
        "flagged_fields": [mean()],
        "overall_mismatches": [mean()],
        "fatals_capped": [mean()],
        "combiner_flags": [mean()],
        "statement_repaired": [mean()],
        "format_retries": [mean()],
        "near_context_limit": [mean()],
    }
)
def panel_checks() -> Scorer:
    """Count what the format checks found in a run. This does not judge the critique.

    Counts, not rates, so that runs can be added up without dividing by zero.
    """

    async def score(state: TaskState, target: Target) -> Score:
        result = store_as(PanelResult)
        answered = [r for r in result.critics if r.verdict is not None]
        checks = [r.checks for r in answered if r.checks is not None]
        combined = result.combined_checks
        usages = [r.usage for r in result.critics] + [
            u
            for u in [
                *(r.retry_usage for r in result.critics),
                result.combiner_usage,
                result.combiner_retry_usage,
                result.repair_usage,
            ]
            if u is not None
        ]
        return Score(
            value={
                "critics_answered": float(len(answered)),
                "quotes": float(sum(len(r.verdict.issues) for r in answered if r.verdict)),
                "quotes_not_verbatim": float(sum(len(c.quotes_not_in_idea) for c in checks)),
                "flagged_fields": float(
                    sum(len(c.cut_off_fields) + len(c.severity_as_issue) for c in checks)
                ),
                "overall_mismatches": float(sum(c.overall_mismatch for c in checks)),
                "fatals_capped": float(sum(len(c.fatal_but_fixable) for c in checks)),
                "combiner_flags": float(combined.count if combined else 0),
                "statement_repaired": float(
                    result.combined.statement_repaired if result.combined else False
                ),
                "format_retries": float(
                    sum(r.format_retried for r in result.critics) + result.combiner_format_retried
                ),
                "near_context_limit": float(sum(u.near_context_limit for u in usages)),
            },
            explanation="; ".join(
                f"{r.critic}: {r.error}" for r in result.critics if r.error is not None
            )
            or None,
        )

    return score

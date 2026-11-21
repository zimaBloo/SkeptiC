"""The critic panel as an Inspect task.

Each critic gets the idea and its own system prompt, and nothing else: no
other critic's answer and no score to anchor on. The critics run one after
another on the models bound to their roles, then the combining step merges
their verdicts and writes the panel's statement. Inspect logs every model
call, so each verdict can be traced back to the exact prompt and reply.

Run it with:

    uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md

Each critic and the combiner is a model role, so any of them can be swapped on
the command line, e.g. --model-role market=ollama/gemma4:12b-it-qat.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    Model,
    ModelOutput,
    ResponseSchema,
    get_model,
)
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import JSONSchema, StoreModel, store_as
from pydantic import BaseModel, Field

from skeptic.checks import CombinedChecks, VerdictChecks, check_combined, check_verdict
from skeptic.combine import (
    CombinedVerdict,
    combined_json_schema,
    combiner_system_prompt,
    parse_combined,
    reviews_prompt,
)
from skeptic.critics import (
    CRITICS,
    CriticName,
    critic_system_prompt,
    prompts_digest,
    submission_prompt,
)
from skeptic.verdict import CriticVerdict, VerdictParseError, parse_verdict, verdict_json_schema

# One model family per critic, chosen from a test of four local models on the
# demo idea (21 Nov 2026). The combiner runs on the model that made the fewest
# factual errors in that test.
DEFAULT_MODELS: dict[str, str] = {
    "market": "ollama/qwen3.5:9b-q4_K_M",
    "technical": "ollama/gemma4:12b-it-qat",
    "customer": "ollama/ministral-3:8b-instruct-2512-q4_K_M",
    "risk": "ollama/nemotron-3-nano:4b-q4_K_M",
    "combiner": "ollama/gemma4:12b-it-qat",
}

# A call that uses this share of the server's context window may have had its
# prompt cut: Ollama shortens a prompt that does not fit, without an error.
CONTEXT_WARNING_SHARE = 0.9


@dataclass(frozen=True)
class Settings:
    """Generation settings shared by every call in a run."""

    thinking: bool
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
    output_tokens: int | None = None
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
    verdict: CriticVerdict | None = None
    extracted: bool = False
    checks: VerdictChecks | None = None
    usage: CallUsage = Field(default_factory=CallUsage)
    error: str | None = None


class PanelResult(StoreModel):
    """Everything the panel produced for one idea, kept in the sample's store."""

    ollama_version: str | None = None
    critics: list[CriticResult] = Field(default_factory=list)
    combiner_model: str | None = None
    combiner_served: ServedModel | None = None
    combiner_usage: CallUsage | None = None
    combiner_error: str | None = None
    combined: CombinedVerdict | None = None
    combined_extracted: bool = False
    combined_checks: CombinedChecks | None = None


@task
def skeptic_panel(
    idea: str,
    thinking: bool = False,
    temperature: float = 0.0,
    seed: int = 1,
    max_tokens: int = 3000,
) -> Task:
    """Run the critic panel on one idea.

    Args:
        idea: Path to a text or Markdown file with the idea.
        thinking: Let models that can think do so before answering. Off by
            default, which is how the default models were tested.
        temperature: Sampling temperature for every call.
        seed: Sampling seed for every call.
        max_tokens: Most tokens any one reply may use.

    Set the generation settings with these task options (-T seed=2), not with
    Inspect's own --temperature or --seed, which the panel's calls override.
    """
    path = Path(idea)
    settings = Settings(thinking, temperature, seed, max_tokens)
    return Task(
        dataset=[
            Sample(
                id=path.stem,
                input=path.read_text(encoding="utf-8"),
                metadata={"idea_file": path.as_posix()},
            )
        ],
        solver=panel(**vars(settings)),
        scorer=panel_checks(),
        # The solver only uses the model roles below, never the main model.
        model="none",
        model_roles=dict(DEFAULT_MODELS),
        metadata={"prompts_sha256": prompts_digest()},
    )


@solver
def panel(
    thinking: bool = False,
    temperature: float = 0.0,
    seed: int = 1,
    max_tokens: int = 3000,
) -> Solver:
    """Run the critics independently, then combine their verdicts."""
    settings = Settings(thinking, temperature, seed, max_tokens)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        idea = state.input_text
        result = store_as(PanelResult)
        result.ollama_version = await ollama_version()

        # One critic after another. With one local model loaded at a time the
        # server would run them in turn anyway, and a fixed order keeps runs
        # repeatable.
        for critic in CRITICS:
            result.critics = [*result.critics, await run_critic(critic, idea, settings)]

        verdicts: dict[CriticName, CriticVerdict | None] = {
            r.critic: r.verdict for r in result.critics
        }
        if not any(verdicts.values()):
            errors = "; ".join(f"{r.critic}: {r.error}" for r in result.critics)
            raise RuntimeError(f"no critic returned a usable verdict ({errors})")

        model = get_model(role="combiner", required=True)
        result.combiner_model = str(model)
        output = await model.generate(
            [
                ChatMessageSystem(content=combiner_system_prompt()),
                ChatMessageUser(content=reviews_prompt(idea, verdicts)),
            ],
            config=generation_config(model, "combined_verdict", combined_json_schema(), settings),
        )
        result.combiner_served = await served_model(model)
        result.combiner_usage = call_usage(output, result.combiner_served)
        try:
            combined, extracted = parse_combined(output.completion)
        except VerdictParseError as error:
            result.combiner_error = with_stop_reason(str(error), output)
            raise VerdictParseError(result.combiner_error) from error
        result.combined = combined
        result.combined_extracted = extracted
        result.combined_checks = check_combined(combined, verdicts)
        state.output = ModelOutput.from_content(model=str(model), content=combined.statement)
        return state

    return solve


async def run_critic(critic: CriticName, idea: str, settings: Settings) -> CriticResult:
    """Ask one critic for its verdict. A reply that cannot be parsed is recorded, not raised."""
    model = get_model(role=critic, required=True)
    output = await model.generate(
        [
            ChatMessageSystem(content=critic_system_prompt(critic)),
            ChatMessageUser(content=submission_prompt(idea)),
        ],
        config=generation_config(model, "critic_verdict", verdict_json_schema(), settings),
    )
    served = await served_model(model)
    result = CriticResult(
        critic=critic, model=str(model), served=served, usage=call_usage(output, served)
    )
    try:
        parsed = parse_verdict(output.completion)
    except VerdictParseError as error:
        result.error = with_stop_reason(str(error), output)
        return result
    result.verdict = parsed.verdict
    result.extracted = parsed.extracted
    result.checks = check_verdict(parsed.verdict, idea)
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
    if settings.thinking:
        return config
    if is_ollama(model):
        # Inspect's Ollama provider drops reasoning_effort="none" (it only passes
        # low, medium and high), and Ollama models that can think do so by default.
        # Ollama's own field turns thinking off.
        return config.merge(GenerateConfig(extra_body={"reasoning": {"effort": "none"}}))
    return config.merge(GenerateConfig(reasoning_effort="none"))


def is_ollama(model: Model) -> bool:
    return str(model).startswith("ollama/")


def call_usage(output: ModelOutput, served: ServedModel | None) -> CallUsage:
    usage = output.usage
    if usage is None:
        return CallUsage(stop_reason=output.stop_reason)
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
        stop_reason=output.stop_reason,
        near_context_limit=window is not None
        and prompt + usage.output_tokens >= CONTEXT_WARNING_SHARE * window,
    )


async def served_model(model: Model) -> ServedModel | None:
    """Ask the Ollama server about a model it has just run. None for other providers."""
    if not is_ollama(model):
        return None
    running = await _ollama_get(model.api.base_url, "/api/ps")
    return parse_served_model(running, model.api.model_name) if running else None


async def ollama_version() -> str | None:
    """The Ollama server's version, if the panel uses Ollama models."""
    for role in DEFAULT_MODELS:
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
        "combiner_flags": [mean()],
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
            u for u in [result.combiner_usage] if u is not None
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
                "combiner_flags": float(
                    0
                    if combined is None
                    else len(combined.critics_without_review)
                    + len(combined.repeated_critics)
                    + len(combined.points_without_critics)
                    + len(combined.one_sided_disagreements)
                    + len(combined.critics_missing_from_points)
                ),
                "near_context_limit": float(sum(u.near_context_limit for u in usages)),
            },
            explanation="; ".join(
                f"{r.critic}: {r.error}" for r in result.critics if r.error is not None
            )
            or None,
        )

    return score

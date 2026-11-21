"""One panel run as a single JSON document.

An Inspect log holds everything about a run, but in Inspect's own structure.
PanelRecord pulls out what a reader or a user interface needs: the idea, each
critic's verdict and timing, the combined verdict, the checks, and enough
provenance (git commit, prompt hash, model builds) to trace the run back.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalLog, read_eval_log
from pydantic import BaseModel, Field, ValidationError

from skeptic.checks import CombinedChecks, VerdictChecks
from skeptic.combine import CombinedVerdict
from skeptic.critics import CRITICS, TITLES, CriticName
from skeptic.panel import RECORD_FORMAT, PanelResult, ServedModel
from skeptic.verdict import CriticVerdict, Overall

# 2: points cite critic issue ids, and the panel works out their severity.
FORMAT_VERSION = RECORD_FORMAT


class CallTiming(BaseModel):
    started_at: datetime | None = None
    finished_at: datetime | None = None
    seconds: float | None = Field(default=None, description="Time the model call took.")


class CriticRecord(BaseModel):
    critic: CriticName
    title: str = Field(description="The critic's angle, e.g. 'technical feasibility'.")
    model: str
    served: ServedModel | None = None
    timing: CallTiming = Field(default_factory=CallTiming)
    verdict: CriticVerdict | None = Field(
        default=None, description="The verdict after the panel's severity rules."
    )
    model_overall: Overall | None = Field(
        default=None, description="The overall rating the critic itself gave."
    )
    capped: list[int] = Field(
        default_factory=list,
        description="Positions of issues the panel lowered from fatal to major.",
    )
    checks: VerdictChecks | None = None
    error: str | None = None


class CombinerRecord(BaseModel):
    model: str | None = None
    served: ServedModel | None = None
    timing: CallTiming = Field(default_factory=CallTiming)
    combined: CombinedVerdict | None = None
    checks: CombinedChecks | None = None
    error: str | None = None
    repair_error: str | None = Field(
        default=None, description="Why a rewrite of an overstated statement was not used."
    )


class PanelRecord(BaseModel):
    """Everything about one panel run on one idea."""

    format_version: Literal[2] = FORMAT_VERSION
    idea_id: str
    idea_file: str | None = None
    idea: str
    status: str = Field(description="'success' or 'error', as Inspect reports the run.")
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log_file: str
    git_commit: str | None = None
    git_dirty: bool | None = Field(
        default=None, description="Whether the code had uncommitted changes."
    )
    prompts_sha256: str | None = None
    inspect_version: str | None = None
    ollama_version: str | None = None
    settings: dict[str, Any] = Field(
        default_factory=dict, description="The task options the run used, apart from the idea."
    )
    critics: list[CriticRecord]
    combiner: CombinerRecord
    scores: dict[str, float] = Field(default_factory=dict)


def record_from_log(log: EvalLog | str | Path) -> PanelRecord:
    """Build the record of a panel run from its Inspect log."""
    if not isinstance(log, EvalLog):
        log = read_eval_log(str(log))
    if not log.samples:
        raise ValueError(f"log has no sample: {log.location}")
    sample = log.samples[0]
    try:
        result = sample.store_as(PanelResult)
    except ValidationError:
        result = None
    if result is None or result.record_format != FORMAT_VERSION:
        revision = log.eval.revision
        written_by = f"commit {revision.commit}" if revision else "an unknown commit"
        if revision and revision.dirty:
            written_by += " with uncommitted changes"
        raise ValueError(
            f"{Path(str(log.location)).name} was written by older panel code ({written_by}). "
            f"This version reads only record format {FORMAT_VERSION}; older logs can still "
            "be opened with 'inspect view'."
        )
    timings = _timings(sample.events)
    by_critic = {r.critic: r for r in result.critics}

    critics = []
    for critic in CRITICS:
        found = by_critic.get(critic)
        critics.append(
            CriticRecord(
                critic=critic,
                title=TITLES[critic],
                model=found.model if found else "",
                served=found.served if found else None,
                timing=timings.get(critic, CallTiming()),
                verdict=found.verdict if found else None,
                model_overall=found.model_overall if found else None,
                capped=found.capped if found else [],
                checks=found.checks if found else None,
                error=found.error if found else "the run stopped before this critic answered",
            )
        )

    score = (sample.scores or {}).get("panel_checks")
    revision = log.eval.revision
    return PanelRecord(
        idea_id=str(sample.id),
        idea_file=(sample.metadata or {}).get("idea_file"),
        idea=sample.input if isinstance(sample.input, str) else str(sample.input),
        status=log.status,
        error=sample.error.message if sample.error else None,
        started_at=_parse_time(sample.started_at),
        finished_at=_parse_time(sample.completed_at),
        log_file=Path(str(log.location)).name,
        git_commit=revision.commit if revision else None,
        git_dirty=revision.dirty if revision else None,
        prompts_sha256=(log.eval.metadata or {}).get("prompts_sha256"),
        inspect_version=(log.eval.packages or {}).get("inspect_ai"),
        ollama_version=result.ollama_version,
        settings={k: v for k, v in (log.eval.task_args or {}).items() if k != "idea"},
        critics=critics,
        combiner=CombinerRecord(
            model=result.combiner_model,
            served=result.combiner_served,
            timing=timings.get("combiner", CallTiming()),
            combined=result.combined,
            checks=result.combined_checks,
            error=result.combiner_error,
            repair_error=result.repair_error,
        ),
        scores=_numbers(score.value if score else None),
    )


def record_json_schema() -> dict[str, Any]:
    """JSON Schema of PanelRecord, for code that reads the records."""
    return PanelRecord.model_json_schema()


def _timings(events: list[Any]) -> dict[str, CallTiming]:
    """Start, end and duration of each role's model calls.

    A role with more than one call (the combiner, when its statement is repaired)
    runs from the start of its first call to the end of its last.
    """
    timings: dict[str, CallTiming] = {}
    for event in events:
        if not isinstance(event, ModelEvent) or not event.role:
            continue
        earlier = timings.get(event.role)
        seconds = (earlier.seconds or 0.0) + (event.working_time or 0.0) if earlier else None
        timings[event.role] = CallTiming(
            started_at=earlier.started_at if earlier else event.timestamp,
            finished_at=event.completed,
            seconds=seconds if earlier else event.working_time,
        )
    return timings


def _numbers(value: object) -> dict[str, float]:
    """The numeric entries of a score value (the panel's scorer returns only numbers)."""
    if not isinstance(value, dict):
        return {}
    return {
        str(key): float(number)
        for key, number in value.items()
        if isinstance(number, int | float) and not isinstance(number, bool)
    }


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)

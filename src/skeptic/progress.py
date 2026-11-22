"""Progress events of a panel run, one JSON object per line.

A user interface follows a run by reading the file while the run writes it.
The events only report what the panel does; writing them never changes what
the panel produces, and a file that cannot be written is skipped with a
warning.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from skeptic.critics import CriticName
from skeptic.verdict import Overall

logger = logging.getLogger(__name__)


class Event(BaseModel):
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Usage(BaseModel):
    """Tokens and cost of a step's model calls, as far as the provider reports them."""

    input_tokens: int | None = None
    output_tokens: int | None = Field(default=None, description="Reasoning tokens included.")
    reasoning_tokens: int | None = None
    cost: float | None = Field(
        default=None, description="Dollars, as the provider billed them (OpenRouter only)."
    )


class RunStarted(Event):
    event: Literal["run_started"] = "run_started"
    critics: list[CriticName] = Field(description="The critics, in the panel's order.")
    models: dict[str, str] = Field(description="The model of every role, the combiner included.")
    parallel: bool = Field(
        default=False, description="The critics run at the same time, not one after another."
    )


class CriticStarted(Event):
    event: Literal["critic_started"] = "critic_started"
    critic: CriticName
    model: str


class CriticFinished(Event):
    event: Literal["critic_finished"] = "critic_finished"
    critic: CriticName
    ok: bool = Field(description="The critic returned a usable verdict.")
    issues: int | None = Field(description="How many issues the verdict lists.")
    overall: Overall | None = Field(description="The overall rating after the severity rules.")
    top_issue: str | None = Field(default=None, description="The critic's most serious issue.")
    error: str | None
    usage: Usage | None = None


class CombinerStarted(Event):
    event: Literal["combiner_started"] = "combiner_started"
    model: str


class RepairStarted(Event):
    """The combiner is asked to rewrite a statement that called a major problem fatal."""

    event: Literal["repair_started"] = "repair_started"


class CombinerFinished(Event):
    event: Literal["combiner_finished"] = "combiner_finished"
    ok: bool
    points: int | None = Field(description="How many points the combined verdict has.")
    error: str | None
    usage: Usage | None = None


class RunFinished(Event):
    event: Literal["run_finished"] = "run_finished"
    ok: bool
    error: str | None


ProgressEvent = Annotated[
    RunStarted
    | CriticStarted
    | CriticFinished
    | CombinerStarted
    | RepairStarted
    | CombinerFinished
    | RunFinished,
    Field(discriminator="event"),
]

_event_adapter: TypeAdapter[ProgressEvent] = TypeAdapter(ProgressEvent)


class ProgressWriter:
    """Appends events to a file, or does nothing when there is no file."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    def write(self, event: Event) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(event.model_dump_json() + "\n")
        except OSError as error:
            logger.warning("progress is not written: %s", error)
            self.path = None


def read_events(path: Path) -> list[ProgressEvent]:
    """The events in a progress file. Lines that do not parse, such as a line
    the run is still writing, are left out."""
    # Bytes, not text: a last line cut off inside a UTF-8 character then fails
    # validation like any broken line, and a U+2028 inside an event's text does
    # not split it.
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    events: list[ProgressEvent] = []
    for line in lines:
        try:
            events.append(_event_adapter.validate_json(line))
        except ValidationError:
            continue
    return events

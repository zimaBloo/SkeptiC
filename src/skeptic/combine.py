"""The combining step: what it returns and the prompt it gets.

The combiner reads the idea and the critics' verdicts. It merges the fatal and
major issues into points, names the critics behind each one, reports where the
critics disagree, and writes the panel's statement. It is told not to add
issues of its own.
"""

import json
from collections.abc import Mapping
from string import Template
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from skeptic.critics import CRITICS, CriticName, load_prompt
from skeptic.verdict import CriticVerdict, json_schema_for, parse_reply


class PanelPoint(BaseModel):
    """One fatal or major problem, with every critic that raised it."""

    model_config = ConfigDict(extra="forbid")

    point: str = Field(description="The problem, in one or two sentences.")
    critics: list[CriticName] = Field(description="Every critic that raised it.")
    severity: Literal["major", "fatal"] = Field(
        description="The highest severity any of these critics gave it."
    )


class Disagreement(BaseModel):
    """Two or more critics making conflicting claims about the same part of the idea."""

    model_config = ConfigDict(extra="forbid")

    point: str = Field(description="What the critics disagree about, and what each one claims.")
    critics: list[CriticName] = Field(description="The critics involved.")


class CombinedVerdict(BaseModel):
    """What the panel returns.

    Points with two or more critics are where the critics agree. The statement
    comes last, so that a model writes it after it has sorted out the points.
    """

    model_config = ConfigDict(extra="forbid")

    points: list[PanelPoint] = Field(
        description="Every fatal or major problem the critics raised, with the same "
        "problem merged into one point."
    )
    disagreements: list[Disagreement] = Field(
        description="Points where the critics make conflicting claims."
    )
    improvements: list[str] = Field(
        description="The most important changes, taken from the critics' suggestions, "
        "most important first."
    )
    statement: str = Field(
        description="The panel's verdict for the author: the most serious problems first, "
        "then what to change."
    )


def combined_json_schema() -> dict[str, Any]:
    """JSON Schema for CombinedVerdict, ready to send to a model."""
    return json_schema_for(CombinedVerdict)


def parse_combined(text: str) -> tuple[CombinedVerdict, bool]:
    """Turn the combiner's raw reply into a validated CombinedVerdict.

    Also returns whether the JSON had to be cut out of surrounding text.
    """
    return parse_reply(text, CombinedVerdict)


def combiner_system_prompt() -> str:
    return load_prompt("combiner.md")


def reviews_prompt(idea: str, verdicts: Mapping[CriticName, CriticVerdict | None]) -> str:
    """The user message for the combiner: the idea and every critic's verdict.

    A critic that gave no usable verdict appears as null, so the combiner can
    say which review is missing.
    """
    reviews = {
        critic: verdict.model_dump() if (verdict := verdicts.get(critic)) else None
        for critic in CRITICS
    }
    return Template(load_prompt("reviews.md")).substitute(
        idea=idea.strip(),
        # Compact JSON, to keep the combiner's input short.
        reviews=json.dumps(reviews, ensure_ascii=False),
    )

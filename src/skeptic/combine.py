"""The combining step: what it returns and the prompt it gets.

The combiner reads the idea and the critics' verdicts, in which every issue
has an id such as 'market-2'. It groups the fatal and major issues into points
by listing the ids each point merges, reports where the critics disagree, and
writes the panel's statement. It does not rate anything: the code works out
each point's critics and severity from the issues it cites, so a point can
neither inflate nor soften what the critics said.
"""

import json
from collections.abc import Collection, Iterable, Mapping
from string import Template
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skeptic.critics import CRITICS, CriticName, load_prompt
from skeptic.verdict import CriticVerdict, Severity, json_schema_for, parse_reply

_SEVERITY_ORDER: dict[Severity, int] = {"minor": 0, "major": 1, "fatal": 2}


# What the combiner model writes.


class ReplyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: str = Field(description="The problem, in one or two sentences.")
    issues: list[str] = Field(description="The ids of the critic issues this point merges.")


class Position(BaseModel):
    """One critic's side of a disagreement."""

    model_config = ConfigDict(extra="forbid")

    critic: CriticName
    claim: str = Field(description="What this critic claims.")


class Disagreement(BaseModel):
    """Two or more critics making conflicting claims about the same part of the idea."""

    model_config = ConfigDict(extra="forbid")

    point: str = Field(description="What the critics disagree about.")
    positions: list[Position] = Field(description="Each critic involved and its claim.")


class CombinerReply(BaseModel):
    """What the combiner model returns. The statement comes last, so that a model
    writes it after it has sorted out the points."""

    model_config = ConfigDict(extra="forbid")

    points: list[ReplyPoint] = Field(
        description="Every fatal or major problem, with the same problem merged into one point."
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


# What the panel keeps, after the code has resolved the issue ids.


class IssueRef(BaseModel):
    """One critic issue that a point merges."""

    id: str
    critic: CriticName
    index: int = Field(description="Position of the issue in the critic's list, from 0.")
    severity: Severity
    capped_from: Severity | None = Field(
        default=None,
        description="The critic's own rating, when the panel lowered it: 'fatal' for a "
        "fatal issue the critic itself called fixable.",
    )


class PanelPoint(BaseModel):
    """One problem, with the critic issues behind it."""

    point: str
    issues: list[IssueRef]
    critics: list[CriticName] = Field(description="The critics whose issues the point merges.")
    severity: Severity = Field(description="The highest severity among the merged issues.")
    severity_split: bool = Field(description="The critics gave this problem different severities.")


class CombinedVerdict(BaseModel):
    """What the panel returns. Points with two or more critics are where they agree."""

    points: list[PanelPoint]
    disagreements: list[Disagreement]
    improvements: list[str]
    statement: str
    unknown_issue_ids: list[str] = Field(
        default_factory=list, description="Ids the combiner cited that match no critic issue."
    )
    statement_repaired: bool = Field(
        default=False,
        description="The first statement called a major problem fatal, so the combiner was "
        "asked once to rewrite it.",
    )
    first_statement: str | None = Field(
        default=None, description="The statement before the repair, if there was one."
    )


class StatementRepair(BaseModel):
    """The combiner's rewritten statement, after it called a major problem fatal."""

    model_config = ConfigDict(extra="forbid")

    statement: str


def issue_id(critic: CriticName, index: int) -> str:
    """The id an issue carries in the combiner's input, e.g. 'market-2' for index 1."""
    return f"{critic}-{index + 1}"


def issue_ids(verdicts: Mapping[CriticName, CriticVerdict | None]) -> list[str]:
    """Every issue id in these verdicts, in critic order."""
    return [
        issue_id(critic, index)
        for critic in CRITICS
        if (verdict := verdicts.get(critic))
        for index in range(len(verdict.issues))
    ]


def combined_json_schema(ids: list[str] | None = None) -> dict[str, Any]:
    """JSON Schema for the combiner's reply, ready to send to a model.

    Given the run's issue ids, the schema allows only those ids in a point, so a
    model under forced output cannot cite an issue that does not exist.
    """
    schema = json_schema_for(CombinerReply)
    if ids:
        id_items = schema["properties"]["points"]["items"]["properties"]["issues"]["items"]
        id_items["enum"] = list(ids)
    return schema


def parse_combined(text: str) -> tuple[CombinerReply, bool]:
    """Turn the combiner's raw reply into a validated CombinerReply.

    Also returns whether the JSON had to be cut out of surrounding text.
    """
    return parse_reply(text, CombinerReply)


def resolve_combined(
    reply: CombinerReply,
    verdicts: Mapping[CriticName, CriticVerdict | None],
    capped: Mapping[CriticName, Collection[int]] | None = None,
) -> CombinedVerdict:
    """Work out each point's critics and severity from the issues it cites.

    capped gives, per critic, the positions of issues the panel lowered from fatal.
    """
    capped = capped or {}
    known = {
        issue_id(critic, index): IssueRef(
            id=issue_id(critic, index),
            critic=critic,
            index=index,
            severity=issue.severity,
            capped_from="fatal" if index in capped.get(critic, ()) else None,
        )
        for critic in CRITICS
        if (verdict := verdicts.get(critic))
        for index, issue in enumerate(verdict.issues)
    }
    unknown: list[str] = []
    points = []
    for reply_point in reply.points:
        refs = []
        for cited in dict.fromkeys(reply_point.issues):  # drop repeats, keep order
            if cited in known:
                refs.append(known[cited])
            else:
                unknown.append(cited)
        per_critic: dict[CriticName, Severity] = {
            critic: _highest(ref.severity for ref in refs if ref.critic == critic)
            for critic in CRITICS
            if any(ref.critic == critic for ref in refs)
        }
        points.append(
            PanelPoint(
                point=reply_point.point,
                issues=refs,
                critics=list(per_critic),
                severity=_highest(per_critic.values()),
                severity_split=len(set(per_critic.values())) > 1,
            )
        )
    return CombinedVerdict(
        points=points,
        disagreements=reply.disagreements,
        improvements=reply.improvements,
        statement=reply.statement,
        unknown_issue_ids=list(dict.fromkeys(unknown)),
    )


def _highest(severities: Iterable[Severity]) -> Severity:
    ordered = sorted(severities, key=_SEVERITY_ORDER.__getitem__)
    return ordered[-1] if ordered else "minor"


def combiner_system_prompt() -> str:
    return load_prompt("combiner.md")


def statement_repair_prompt() -> str:
    """The follow-up message when the statement calls a major problem fatal."""
    return load_prompt("statement_repair.md")


def repair_json_schema() -> dict[str, Any]:
    return json_schema_for(StatementRepair)


def parse_repair(text: str) -> StatementRepair:
    repair, _ = parse_reply(text, StatementRepair)
    return repair


def reviews_prompt(
    idea: str,
    verdicts: Mapping[CriticName, CriticVerdict | None],
    capped: Mapping[CriticName, Collection[int]] | None = None,
) -> str:
    """The user message for the combiner: the idea and every critic's verdict.

    Each issue carries its id, and an issue the panel lowered from fatal says so
    in capped_from, because the critic's own words may still call it fatal. A
    critic that gave no usable verdict appears as null, so the combiner can say
    which review is missing.
    """
    capped = capped or {}
    reviews: dict[str, Any] = {}
    for critic in CRITICS:
        verdict = verdicts.get(critic)
        if verdict is None:
            reviews[critic] = None
            continue
        dumped = verdict.model_dump()
        issues = []
        for index, issue in enumerate(dumped["issues"]):
            entry = {"id": issue_id(critic, index), **issue}
            if index in capped.get(critic, ()):
                entry["capped_from"] = "fatal"
            issues.append(entry)
        dumped["issues"] = issues
        reviews[critic] = dumped
    return Template(load_prompt("reviews.md")).substitute(
        idea=idea.strip(),
        # Compact JSON, to keep the combiner's input short.
        reviews=json.dumps(reviews, ensure_ascii=False),
    )

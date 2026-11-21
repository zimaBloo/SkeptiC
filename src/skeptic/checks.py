"""Checks that run on every answer the panel gets.

A reply can match the schema and still be broken: a quote the idea never
contains, a text field that stops mid-sentence, a rating that does not follow
from the issues, a combined verdict that names a critic who never answered.
These checks flag such replies in the log. They never change or reject one.
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel, Field

from skeptic.combine import CombinedVerdict
from skeptic.critics import CriticName
from skeptic.verdict import CriticVerdict, Overall

SEVERITY_WORDS = frozenset({"minor", "major", "fatal"})

# Endings that mean a text field stopped mid-sentence. Under forced JSON output,
# a model that writes a double quote inside a field closes the string early,
# which leaves fields ending in things like "(e.g., ".
_CUT_OFF_ENDINGS = ("(", ",", ":", ";", " e.g.", " i.e.", " such as", " and", " or")

# Characters that differ between a faithful quote and the idea for reasons that
# do not change the words: quote marks of every kind and Markdown emphasis are
# dropped, dashes become hyphens, and the ellipsis character becomes three dots.
_DROPPED = "'\"\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u00ab\u00bb*_`"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2212"
_TRANSLATION = str.maketrans(
    {**dict.fromkeys(map(ord, _DROPPED)), **dict.fromkeys(map(ord, _DASHES), "-"), 0x2026: "..."}
)


class VerdictChecks(BaseModel):
    """What the checks found in one critic's verdict. Empty means nothing was flagged."""

    quotes_not_in_idea: list[str] = Field(
        default_factory=list,
        description="Quotes that do not appear word for word in the idea.",
    )
    cut_off_fields: list[str] = Field(
        default_factory=list,
        description="Text fields that look cut off, e.g. 'issues[2].how_to_improve'.",
    )
    severity_as_issue: list[str] = Field(
        default_factory=list,
        description="Issue fields that hold only a severity word.",
    )
    overall_mismatch: bool = Field(
        default=False,
        description="The overall rating is not the one the issues' severities give.",
    )

    @property
    def flagged(self) -> bool:
        return bool(
            self.quotes_not_in_idea
            or self.cut_off_fields
            or self.severity_as_issue
            or self.overall_mismatch
        )


class CombinedChecks(BaseModel):
    """What the checks found in the combined verdict. Empty means nothing was flagged."""

    critics_without_review: list[str] = Field(
        default_factory=list,
        description="Critics named in a point or disagreement who gave no usable review, "
        "e.g. 'points[0]: risk'.",
    )
    repeated_critics: list[str] = Field(
        default_factory=list,
        description="Points or disagreements that name the same critic twice.",
    )
    points_without_critics: list[str] = Field(
        default_factory=list,
        description="Points that name no critic, so nobody on the panel raised them.",
    )
    one_sided_disagreements: list[str] = Field(
        default_factory=list,
        description="Disagreements that name fewer than two critics.",
    )
    critics_missing_from_points: list[CriticName] = Field(
        default_factory=list,
        description="Critics who raised a fatal or major issue but appear in no point.",
    )

    @property
    def flagged(self) -> bool:
        return bool(
            self.critics_without_review
            or self.repeated_critics
            or self.points_without_critics
            or self.one_sided_disagreements
            or self.critics_missing_from_points
        )


def check_verdict(verdict: CriticVerdict, idea: str) -> VerdictChecks:
    """Run every check on one critic's verdict."""
    checks = VerdictChecks(overall_mismatch=verdict.overall != overall_from_issues(verdict))
    if looks_cut_off(verdict.summary):
        checks.cut_off_fields.append("summary")
    for index, issue in enumerate(verdict.issues):
        if not quote_in_idea(issue.quote, idea):
            checks.quotes_not_in_idea.append(issue.quote)
        if issue.issue.strip().rstrip(".").lower() in SEVERITY_WORDS:
            checks.severity_as_issue.append(f"issues[{index}].issue")
        for field in ("issue", "why_it_matters", "how_to_improve"):
            if looks_cut_off(getattr(issue, field)):
                checks.cut_off_fields.append(f"issues[{index}].{field}")
    return checks


def check_combined(
    combined: CombinedVerdict, verdicts: Mapping[CriticName, CriticVerdict | None]
) -> CombinedChecks:
    """Check the combined verdict against the critics' own verdicts."""
    checks = CombinedChecks()
    answered = {critic for critic, verdict in verdicts.items() if verdict is not None}
    entries = [(f"points[{i}]", p.critics) for i, p in enumerate(combined.points)] + [
        (f"disagreements[{i}]", d.critics) for i, d in enumerate(combined.disagreements)
    ]
    for where, critics in entries:
        checks.critics_without_review += [f"{where}: {c}" for c in critics if c not in answered]
        if len(set(critics)) < len(critics):
            checks.repeated_critics.append(where)
    for index, point in enumerate(combined.points):
        if not point.critics:
            checks.points_without_critics.append(f"points[{index}]")
    for index, disagreement in enumerate(combined.disagreements):
        if len(set(disagreement.critics)) < 2:
            checks.one_sided_disagreements.append(f"disagreements[{index}]")
    in_points = {critic for point in combined.points for critic in point.critics}
    for critic, verdict in verdicts.items():
        serious = verdict is not None and any(i.severity != "minor" for i in verdict.issues)
        if serious and critic not in in_points:
            checks.critics_missing_from_points.append(critic)
    return checks


def overall_from_issues(verdict: CriticVerdict) -> Overall:
    """The overall rating that the prompt's rule gives for these issues."""
    severities = [issue.severity for issue in verdict.issues]
    if "fatal" in severities:
        return "not viable"
    majors = severities.count("major")
    if majors >= 3:
        return "weak"
    if majors >= 1:
        return "promising"
    return "strong"


def quote_in_idea(quote: str, idea: str) -> bool:
    """Whether a quote appears word for word in the idea.

    Ignored: case, spacing, quote marks, Markdown emphasis (* _ `), the kind of
    dash, and punctuation at either end of the quote. Nothing else is: a quote
    with words left out, added or changed, or one that stops inside a word or
    number, does not count.
    """
    wanted = _normalise(quote).strip(" .,:;")
    if not wanted:
        return False
    pattern = r"(?<!\w)" + re.escape(wanted) + r"(?!\w)"
    return re.search(pattern, _normalise(idea)) is not None


def looks_cut_off(text: str) -> bool:
    """Whether a text field seems to stop mid-sentence."""
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith(_CUT_OFF_ENDINGS):
        return True
    return stripped.rfind("(") > stripped.rfind(")")


def _normalise(text: str) -> str:
    return " ".join(text.translate(_TRANSLATION).lower().split())

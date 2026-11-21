"""Checks that run on every answer the panel gets.

A reply can match the schema and still be broken: a quote the idea never
contains, a text field that stops mid-sentence, a rating that does not follow
from the issues, a combined verdict that names a critic who never answered.
These checks flag such replies in the log. They never change or reject one.
"""

import re
from collections.abc import Mapping

from pydantic import BaseModel, Field

from skeptic.combine import CombinedVerdict, issue_id
from skeptic.critics import CRITICS, CriticName
from skeptic.verdict import CriticVerdict, overall_from_issues

SEVERITY_WORDS = frozenset({"minor", "major", "fatal"})

# Endings that mean a text field stopped mid-sentence. Under forced JSON output,
# a model that writes a double quote inside a field closes the string early,
# which leaves fields ending in things like "(e.g., ".
_CUT_OFF_ENDINGS = ("(", ",", ":", ";", " e.g.", " i.e.", " such as", " and", " or")

# Phrases that make a statement sound fatal, and words that deny such a phrase.
_FATAL_WORDS = re.compile(
    r"(?<!non-)\bfatal(?:ly)?\b|\bnot (?:\w+ ){0,2}?viable\b|\b(?:non-?viable|unviable)\b",
    re.IGNORECASE,
)
_NEGATIONS = frozenset(
    {
        "no",
        "not",
        "none",
        "nothing",
        "never",
        "without",
        "neither",
        "nor",
        "hardly",
        "cannot",
        "can't",
        "won't",
        "wouldn't",
        "isn't",
        "aren't",
        "wasn't",
        "weren't",
        "didn't",
        "doesn't",
        "don't",
    }
)
_CLAUSE_OPENERS = frozenset({"no", "none", "neither", "nothing", "nobody"})
_CLAUSE_BREAK = re.compile(
    r"[.;:!?\n]|\b(?:but|because|and|while|so|which|although|though|whereas)\b",
    re.IGNORECASE,
)
# How many words before a match a negation may be and still deny it.
_NEGATION_WINDOW = 5

# Characters that differ between a faithful quote and the idea for reasons that
# do not change the words: quote marks of every kind and Markdown emphasis are
# dropped, dashes become hyphens, and the ellipsis character becomes three dots.
_DROPPED = "'\"\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u00ab\u00bb*_`"
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2212"
_TRANSLATION_APOSTROPHES = str.maketrans({0x2018: "'", 0x2019: "'"})
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
        description="The critic's own overall rating is not the one its severities give.",
    )
    fatal_but_fixable: list[str] = Field(
        default_factory=list,
        description="Issues marked fatal although the critic called them fixable within the "
        "idea. The panel caps these at major.",
    )

    @property
    def flagged(self) -> bool:
        return bool(
            self.quotes_not_in_idea
            or self.cut_off_fields
            or self.severity_as_issue
            or self.overall_mismatch
            or self.fatal_but_fixable
        )


class CombinedChecks(BaseModel):
    """What the checks found in the combined verdict. Empty means nothing was flagged."""

    dropped_issues: list[str] = Field(
        default_factory=list,
        description="Fatal or major critic issues that appear in no point, e.g. 'risk-2'.",
    )
    minor_only_points: list[str] = Field(
        default_factory=list,
        description="Points made only of minor issues.",
    )
    points_without_issues: list[str] = Field(
        default_factory=list,
        description="Points that cite no real critic issue, so nobody on the panel raised them.",
    )
    unknown_issue_ids: list[str] = Field(
        default_factory=list,
        description="Ids the combiner cited that match no critic issue.",
    )
    one_sided_disagreements: list[str] = Field(
        default_factory=list,
        description="Disagreements with fewer than two critics.",
    )
    critics_without_review: list[str] = Field(
        default_factory=list,
        description="Critics named in a disagreement who gave no usable review, "
        "e.g. 'disagreements[0]: risk'.",
    )
    statement_overstates: bool = Field(
        default=False,
        description="The statement calls something fatal or not viable, but no critic issue "
        "is fatal (after the panel's severity rules).",
    )

    @property
    def flagged(self) -> bool:
        return self.count > 0

    @property
    def count(self) -> int:
        """How many things were flagged in all."""
        return (
            len(self.dropped_issues)
            + len(self.minor_only_points)
            + len(self.points_without_issues)
            + len(self.unknown_issue_ids)
            + len(self.one_sided_disagreements)
            + len(self.critics_without_review)
            + int(self.statement_overstates)
        )


def check_verdict(verdict: CriticVerdict, idea: str) -> VerdictChecks:
    """Run every check on one critic's verdict, as the critic wrote it."""
    checks = VerdictChecks(overall_mismatch=verdict.overall != overall_from_issues(verdict))
    if looks_cut_off(verdict.summary):
        checks.cut_off_fields.append("summary")
    for index, issue in enumerate(verdict.issues):
        if not quote_in_idea(issue.quote, idea):
            checks.quotes_not_in_idea.append(issue.quote)
        if issue.issue.strip().rstrip(".").lower() in SEVERITY_WORDS:
            checks.severity_as_issue.append(f"issues[{index}].issue")
        if issue.severity == "fatal" and issue.fixable_within_idea:
            checks.fatal_but_fixable.append(f"issues[{index}]")
        for field in ("issue", "why_it_matters", "how_to_improve"):
            if looks_cut_off(getattr(issue, field)):
                checks.cut_off_fields.append(f"issues[{index}].{field}")
    return checks


def check_combined(
    combined: CombinedVerdict, verdicts: Mapping[CriticName, CriticVerdict | None]
) -> CombinedChecks:
    """Check the combined verdict against the critics' own verdicts."""
    checks = CombinedChecks(unknown_issue_ids=list(combined.unknown_issue_ids))
    cited = {ref.id for point in combined.points for ref in point.issues}
    for critic in CRITICS:
        verdict = verdicts.get(critic)
        for index, issue in enumerate(verdict.issues if verdict else []):
            if issue.severity != "minor" and issue_id(critic, index) not in cited:
                checks.dropped_issues.append(issue_id(critic, index))
    for index, point in enumerate(combined.points):
        if not point.issues:
            checks.points_without_issues.append(f"points[{index}]")
        elif point.severity == "minor":
            checks.minor_only_points.append(f"points[{index}]")
    answered = {critic for critic, verdict in verdicts.items() if verdict is not None}
    for index, disagreement in enumerate(combined.disagreements):
        critics = [position.critic for position in disagreement.positions]
        if len(set(critics)) < 2:
            checks.one_sided_disagreements.append(f"disagreements[{index}]")
        checks.critics_without_review += [
            f"disagreements[{index}]: {c}" for c in dict.fromkeys(critics) if c not in answered
        ]
    no_fatal_issue = not any(
        issue.severity == "fatal"
        for verdict in verdicts.values()
        if verdict is not None
        for issue in verdict.issues
    )
    checks.statement_overstates = no_fatal_issue and calls_fatal(combined.statement)
    return checks


def calls_fatal(text: str) -> bool:
    """Whether a text calls something fatal or not viable, and does not deny it.

    'The pricing is a fatal flaw' and 'not a weakness but a fatal flaw' count.
    'No problem is fatal', 'the issues are non-fatal' and 'the critic did not call
    it not viable' do not: a match is skipped when a negation comes a few words
    before it, or opens its clause. Clauses break at punctuation and at words such
    as 'but' and 'because'.
    """
    text = text.translate(_TRANSLATION_APOSTROPHES)
    for clause in _CLAUSE_BREAK.split(text):
        words_in_clause = re.findall(r"[\w']+", clause.lower())
        opens_with_denial = bool(words_in_clause) and words_in_clause[0] in _CLAUSE_OPENERS
        for match in _FATAL_WORDS.finditer(clause):
            before = re.findall(r"[\w']+", clause[: match.start()].lower())
            if opens_with_denial or _NEGATIONS.intersection(before[-_NEGATION_WINDOW:]):
                continue
            return True
    return False


def is_complete_sentence(text: str) -> bool:
    """Whether a text is non-empty, not cut off, and ends like a finished sentence."""
    stripped = text.strip()
    return bool(stripped) and not looks_cut_off(stripped) and stripped[-1] in ".!?\"')]"


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

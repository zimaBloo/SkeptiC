import json
from typing import Any

import pytest

from skeptic.combine import (
    CombinerReply,
    combined_json_schema,
    combiner_system_prompt,
    issue_ids,
    parse_combined,
    resolve_combined,
    reviews_prompt,
)
from skeptic.critics import CriticName
from skeptic.verdict import CriticVerdict, Issue, VerdictParseError


def issue(severity: str) -> Issue:
    return Issue(
        quote="It costs €149.",
        issue="Too expensive.",
        why_it_matters="Few will buy it.",
        how_to_improve="Lower the price.",
        fixable_within_idea=True,
        severity=severity,  # type: ignore[arg-type]
    )


def verdict(*severities: str) -> CriticVerdict:
    issues = [issue(s) for s in severities]
    return CriticVerdict(issues=issues, summary="Too expensive.", overall="weak")


VERDICTS: dict[CriticName, CriticVerdict | None] = {
    "market": verdict("major", "minor"),
    "technical": None,
    "customer": verdict("fatal"),
    "risk": verdict("minor"),
}


def reply(**changes: Any) -> CombinerReply:
    fields: dict[str, Any] = {
        "points": [{"point": "Price", "issues": ["market-1", "customer-1"]}],
        "disagreements": [],
        "improvements": ["Lower the price."],
        "statement": "The price is too high.",
    }
    return CombinerReply.model_validate(fields | changes)


def test_reviews_prompt_gives_every_issue_an_id_and_marks_missing_reviews() -> None:
    prompt = reviews_prompt("The idea.", VERDICTS)

    reviews = json.loads(prompt.split("as JSON:", 1)[1])
    assert list(reviews) == ["market", "technical", "customer", "risk"]
    assert [i["id"] for i in reviews["market"]["issues"]] == ["market-1", "market-2"]
    assert reviews["technical"] is None
    assert prompt.startswith("The idea:\n\nThe idea.")


def test_issue_ids_follow_critic_order() -> None:
    assert issue_ids(VERDICTS) == ["market-1", "market-2", "customer-1", "risk-1"]


def test_schema_allows_only_the_runs_issue_ids() -> None:
    schema = combined_json_schema(["market-1", "risk-1"])

    ids = schema["properties"]["points"]["items"]["properties"]["issues"]["items"]
    assert ids["enum"] == ["market-1", "risk-1"]
    assert "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert list(schema["properties"])[-1] == "statement"


def test_point_severity_and_critics_come_from_the_cited_issues() -> None:
    combined = resolve_combined(reply(), VERDICTS)

    [point] = combined.points
    assert point.critics == ["market", "customer"]
    assert point.severity == "fatal"
    assert point.severity_split
    assert [ref.id for ref in point.issues] == ["market-1", "customer-1"]


def test_unknown_and_repeated_ids_are_set_aside() -> None:
    points = [{"point": "Price", "issues": ["market-1", "market-1", "technical-1"]}]

    combined = resolve_combined(reply(points=points), VERDICTS)

    [point] = combined.points
    assert [ref.id for ref in point.issues] == ["market-1"]
    assert point.critics == ["market"]
    assert not point.severity_split
    assert combined.unknown_issue_ids == ["technical-1"]


def test_combiner_reply_parses_and_rejects_unknown_critics() -> None:
    disagreement = {"point": "Price", "positions": [{"critic": "finance", "claim": "Fine."}]}
    good = reply().model_dump()
    bad = {**good, "disagreements": [disagreement]}

    parsed, extracted = parse_combined(json.dumps(good))
    assert not extracted and parsed == reply()
    with pytest.raises(VerdictParseError):
        parse_combined(json.dumps(bad))


def test_combiner_prompt_lists_the_fields_in_schema_order() -> None:
    prompt = combiner_system_prompt()
    answer = prompt[prompt.index("Answer in English") :]

    positions = [answer.index(f"- {field}:") for field in combined_json_schema()["properties"]]
    assert positions == sorted(positions)


def test_severity_split_compares_critics_not_issues() -> None:
    verdicts: dict[CriticName, CriticVerdict | None] = {
        "market": verdict("major", "minor"),
        "customer": verdict("major", "minor"),
    }
    one_critic = reply(points=[{"point": "Price", "issues": ["market-1", "market-2"]}])
    minor_aside = reply(
        points=[{"point": "Price", "issues": ["market-1", "customer-1", "customer-2"]}]
    )

    assert not resolve_combined(one_critic, verdicts).points[0].severity_split
    assert not resolve_combined(minor_aside, verdicts).points[0].severity_split


def test_capped_issues_are_marked_for_the_combiner_and_the_record() -> None:
    capped: dict[CriticName, list[int]] = {"customer": [0]}

    prompt = reviews_prompt("The idea.", VERDICTS, capped)
    combined = resolve_combined(reply(), VERDICTS, capped)

    reviews = json.loads(prompt.split("as JSON:", 1)[1])
    assert reviews["customer"]["issues"][0]["capped_from"] == "fatal"
    assert "capped_from" not in reviews["market"]["issues"][0]
    refs = {ref.id: ref.capped_from for ref in combined.points[0].issues}
    assert refs == {"market-1": None, "customer-1": "fatal"}

import json

import pytest

from skeptic.combine import (
    combined_json_schema,
    combiner_system_prompt,
    parse_combined,
    reviews_prompt,
)
from skeptic.verdict import CriticVerdict, VerdictParseError

VERDICT = CriticVerdict(issues=[], summary="Too expensive.", overall="strong")

COMBINED = {
    "points": [{"point": "Price", "critics": ["market", "customer"], "severity": "major"}],
    "disagreements": [],
    "improvements": ["Raise the price."],
    "statement": "Weak idea.",
}


def test_reviews_prompt_lists_every_critic_and_marks_missing_ones() -> None:
    prompt = reviews_prompt("The idea.", {"market": VERDICT, "risk": None})

    reviews = json.loads(prompt.split("as JSON:", 1)[1])
    assert list(reviews) == ["market", "technical", "customer", "risk"]
    assert reviews["market"]["summary"] == "Too expensive."
    assert reviews["technical"] is None
    assert reviews["risk"] is None
    assert prompt.startswith("The idea:\n\nThe idea.")


def test_combined_schema_is_self_contained_and_strict() -> None:
    schema = combined_json_schema()

    assert "$ref" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["points"]["items"]["additionalProperties"] is False
    assert list(schema["properties"])[-1] == "statement"


def test_combined_reply_parses() -> None:
    combined, extracted = parse_combined(json.dumps(COMBINED))

    assert not extracted
    assert combined.points[0].critics == ["market", "customer"]


def test_combined_verdict_rejects_unknown_critics_and_minor_points() -> None:
    unknown_critic = {**COMBINED, "points": [{**COMBINED["points"][0], "critics": ["finance"]}]}
    minor_point = {**COMBINED, "points": [{**COMBINED["points"][0], "severity": "minor"}]}

    for reply in (unknown_critic, minor_point):
        with pytest.raises(VerdictParseError):
            parse_combined(json.dumps(reply))


def test_combiner_prompt_lists_the_fields_in_schema_order() -> None:
    prompt = combiner_system_prompt()
    answer = prompt[prompt.index("Answer in English") :]

    positions = [answer.index(f"- {field}:") for field in combined_json_schema()["properties"]]
    assert positions == sorted(positions)

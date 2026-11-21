import json
from typing import Any

import pytest

from skeptic.verdict import (
    CriticVerdict,
    VerdictParseError,
    parse_verdict,
    verdict_json_schema,
)

VALID: dict[str, Any] = {
    "summary": "The camera is sold below cost and the accuracy claim has no evidence.",
    "issues": [
        {
            "quote": "it costs about €160 to make",
            "issue": "Each camera is sold at a loss.",
            "why_it_matters": "More sales mean bigger losses until the subscription covers them.",
            "how_to_improve": "Show the unit cost needed to break even and how to reach it.",
            "severity": "fatal",
        }
    ],
    "overall": "weak",
}


def test_clean_json_reply_parses() -> None:
    parsed = parse_verdict(json.dumps(VALID))

    assert parsed.extracted is False
    assert parsed.verdict.overall == "weak"
    assert parsed.verdict.issues[0].severity == "fatal"


def test_json_inside_prose_or_code_fence_is_extracted_and_flagged() -> None:
    reply = "Here is my review:\n```json\n" + json.dumps(VALID) + "\n```\nHope this helps."

    parsed = parse_verdict(reply)

    assert parsed.extracted is True
    assert parsed.verdict == CriticVerdict.model_validate(VALID)


def test_unknown_severity_is_rejected() -> None:
    bad = json.loads(json.dumps(VALID))
    bad["issues"][0]["severity"] = "catastrophic"

    with pytest.raises(VerdictParseError):
        parse_verdict(json.dumps(bad))


def test_unexpected_field_is_rejected() -> None:
    bad = {**VALID, "score": 7}

    with pytest.raises(VerdictParseError):
        parse_verdict(json.dumps(bad))


def test_reply_without_json_is_rejected() -> None:
    with pytest.raises(VerdictParseError, match="no JSON object"):
        parse_verdict("I cannot review this idea.")


def test_schema_sent_to_models_is_self_contained() -> None:
    text = json.dumps(verdict_json_schema())

    assert "$ref" not in text
    assert "$defs" not in text


def test_schema_avoids_keywords_some_providers_reject() -> None:
    # Anthropic's strict schema mode rejects numeric and length limits.
    text = json.dumps(verdict_json_schema())

    for keyword in ("minimum", "maximum", "minLength", "maxLength", "maxItems", "pattern"):
        assert f'"{keyword}"' not in text


def test_schema_forbids_extra_fields_everywhere() -> None:
    schema = verdict_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["issues"]["items"]["additionalProperties"] is False


def test_issues_come_before_summary_and_rating() -> None:
    # A model writes fields in schema order, so it should work out the issues first.
    assert list(verdict_json_schema()["properties"]) == ["issues", "summary", "overall"]

import json
from typing import Any

import pytest

from skeptic.verdict import (
    CriticVerdict,
    Issue,
    VerdictParseError,
    apply_severity_rules,
    overall_from_issues,
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
            "fixable_within_idea": False,
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


def issue(severity: str, fixable: bool) -> Issue:
    return Issue.model_validate(
        {
            **VALID["issues"][0],
            "severity": severity,
            "fixable_within_idea": fixable,
        }
    )


def verdict(*issues: Issue, overall: Any = "not viable") -> CriticVerdict:
    return CriticVerdict(issues=list(issues), summary="A summary.", overall=overall)


def test_overall_follows_the_prompts_rule() -> None:
    assert overall_from_issues(verdict(issue("fatal", False), issue("major", True))) == "not viable"
    assert overall_from_issues(verdict(*[issue("major", True)] * 3)) == "weak"
    assert overall_from_issues(verdict(issue("major", True), issue("minor", True))) == "promising"
    assert overall_from_issues(verdict(issue("minor", True))) == "strong"
    assert overall_from_issues(verdict()) == "strong"


def test_fatal_issue_the_critic_calls_fixable_is_capped_at_major() -> None:
    original = verdict(issue("fatal", True), issue("fatal", False), issue("major", True))

    adjusted, capped = apply_severity_rules(original)

    assert capped == [0]
    assert [i.severity for i in adjusted.issues] == ["major", "fatal", "major"]
    assert adjusted.overall == "not viable"  # the unfixable fatal stays
    assert original.issues[0].severity == "fatal"  # the critic's verdict is not changed


def test_overall_is_worked_out_again_after_capping() -> None:
    adjusted, capped = apply_severity_rules(verdict(issue("fatal", True), overall="not viable"))

    assert capped == [0]
    assert adjusted.overall == "promising"

from typing import Any

from skeptic.checks import (
    check_combined,
    check_verdict,
    looks_cut_off,
    quote_in_idea,
)
from skeptic.combine import CombinedVerdict
from skeptic.critics import CriticName
from skeptic.verdict import CriticVerdict, Issue

IDEA = """**Solution.** A small battery-powered camera clips inside the fridge door and
photographs the shelves every time the door closes. It costs €149.

**Market.** There are no direct competitors: smart fridges are expensive, and recipe
apps don't know what you have. Customers call it "the fridge spy" - in a good way."""

CURLY_APOSTROPHE = chr(0x2019)
EN_DASH = chr(0x2013)
ELLIPSIS = chr(0x2026)


def issue(**changes: Any) -> Issue:
    fields = {
        "quote": "There are no direct competitors",
        "issue": "Competitors exist.",
        "why_it_matters": "The plan ignores them.",
        "how_to_improve": "List the competitors and how the product differs.",
        "fixable_within_idea": True,
        "severity": "major",
    }
    return Issue.model_validate(fields | changes)


def verdict(*issues: Issue, overall: Any = "promising") -> CriticVerdict:
    return CriticVerdict(issues=list(issues), summary="Competitors exist.", overall=overall)


def test_quote_matches_across_line_breaks_markdown_and_quote_marks() -> None:
    assert quote_in_idea(f"**recipe apps don{CURLY_APOSTROPHE}t know what you have.**", IDEA)
    assert quote_in_idea("'photographs the shelves every time the door closes'", IDEA)
    assert quote_in_idea("fridge door and photographs", IDEA)
    # The prompt asks for double quotes inside a passage to become single quotes.
    assert quote_in_idea("Customers call it 'the fridge spy' - in a good way", IDEA)
    assert quote_in_idea("Customers call it the fridge spy", IDEA)


def test_quote_matches_other_dashes_and_a_trailing_ellipsis() -> None:
    assert quote_in_idea(f"the fridge spy {EN_DASH} in a good way", IDEA)
    assert quote_in_idea(f"smart fridges are expensive{ELLIPSIS}", IDEA)
    assert quote_in_idea("smart fridges are expensive...", IDEA)


def test_quote_with_words_left_out_or_added_does_not_match() -> None:
    # Both seen in real replies: a silent word drop and a bracketed insertion.
    assert not quote_in_idea(
        "The camera clips inside the fridge door and photographs the shelves", IDEA
    )
    assert not quote_in_idea("There are no direct competitors [in Finland]", IDEA)
    assert not quote_in_idea("", IDEA)


def test_quote_that_stops_inside_a_word_or_number_does_not_match() -> None:
    assert quote_in_idea("It costs €149", IDEA)
    assert not quote_in_idea("It costs €14", IDEA)
    assert not quote_in_idea("ompetitors", IDEA)


def test_cut_off_text_is_detected() -> None:
    # The first case is a real reply: a double quote inside the field closed the string.
    assert looks_cut_off("Bundle with a grocery delivery service (e.g., ")
    assert looks_cut_off("Use cheaper parts, such as")
    assert looks_cut_off("The price (149 euros is too high")
    assert not looks_cut_off("Reprice the camera (or subsidise it) to cover its cost.")
    assert not looks_cut_off("")


def test_check_verdict_reports_where_each_problem_is() -> None:
    checked = CriticVerdict(
        issues=[
            issue(),
            issue(quote="made-up words", issue="Major"),
            issue(how_to_improve="Partner with a retailer (e.g., "),
            issue(severity="fatal"),
        ],
        summary="Competitors exist (e.g., ",
        overall="weak",
    )

    checks = check_verdict(checked, IDEA)

    assert checks.flagged
    assert checks.quotes_not_in_idea == ["made-up words"]
    assert checks.severity_as_issue == ["issues[1].issue"]
    assert checks.cut_off_fields == ["summary", "issues[2].how_to_improve"]
    assert checks.overall_mismatch  # a fatal issue gives 'not viable', so 'weak' is wrong
    assert checks.fatal_but_fixable == ["issues[3]"]


def test_clean_verdict_is_not_flagged() -> None:
    assert not check_verdict(verdict(issue()), IDEA).flagged


def test_check_combined_flags_points_that_do_not_match_the_reviews() -> None:
    combined = CombinedVerdict.model_validate(
        {
            "points": [
                {"point": "Competitors exist.", "critics": ["market", "risk"], "severity": "major"},
                {"point": "Too costly.", "critics": ["market", "market"], "severity": "major"},
                {"point": "Made up.", "critics": [], "severity": "fatal"},
            ],
            "disagreements": [{"point": "Severity of the price.", "critics": ["market"]}],
            "improvements": [],
            "statement": "Weak.",
        }
    )
    verdicts: dict[CriticName, CriticVerdict | None] = {
        "market": verdict(issue()),
        "technical": None,
        "customer": verdict(issue()),
        "risk": None,
    }

    checks = check_combined(combined, verdicts)

    assert checks.critics_without_review == ["points[0]: risk"]
    assert checks.repeated_critics == ["points[1]"]
    assert checks.points_without_critics == ["points[2]"]
    assert checks.one_sided_disagreements == ["disagreements[0]"]
    assert checks.critics_missing_from_points == ["customer"]


def test_faithful_combined_verdict_is_not_flagged() -> None:
    combined = CombinedVerdict.model_validate(
        {
            "points": [
                {
                    "point": "Competitors exist.",
                    "critics": ["market", "customer"],
                    "severity": "major",
                }
            ],
            "disagreements": [],
            "improvements": ["Name the competitors."],
            "statement": "Weak.",
        }
    )
    verdicts: dict[CriticName, CriticVerdict | None] = {
        "market": verdict(issue()),
        "customer": verdict(issue(severity="minor")),
    }

    assert not check_combined(combined, verdicts).flagged

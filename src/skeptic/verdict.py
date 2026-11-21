"""The structure every critic answers in, and how a raw model reply is turned into it."""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Severity = Literal["minor", "major", "fatal"]
Overall = Literal["strong", "promising", "weak", "not viable"]


class Issue(BaseModel):
    """One weakness or risk, tied to the words in the idea it is about."""

    model_config = ConfigDict(extra="forbid")

    quote: str = Field(description="The exact words from the idea that this issue is about.")
    issue: str = Field(description="What is weak or risky.")
    why_it_matters: str = Field(description="Why it is a problem for the idea.")
    how_to_improve: str = Field(description="A concrete change that would fix or reduce it.")
    severity: Severity


class CriticVerdict(BaseModel):
    """What one critic returns: the issues, then a short summary and a rating.

    The issues come first so that a model writes the summary and the rating after
    it has worked out the issues, not before.
    """

    model_config = ConfigDict(extra="forbid")

    issues: list[Issue] = Field(description="The most important problems, most serious first.")
    summary: str = Field(description="Two to four sentences on the main weaknesses.")
    overall: Overall


class VerdictParseError(ValueError):
    """Raised when a model reply cannot be turned into the expected structure."""


@dataclass(frozen=True)
class ParsedVerdict:
    verdict: CriticVerdict
    # True when the JSON had to be cut out of surrounding text, e.g. from a model
    # that cannot be forced into JSON output. Logged so format slips stay visible.
    extracted: bool


def verdict_json_schema() -> dict[str, Any]:
    """JSON Schema for CriticVerdict, ready to send to a model."""
    return json_schema_for(CriticVerdict)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a pydantic model with every $ref inlined.

    Some providers and local engines handle $ref poorly, so the schema sent to a
    model is self-contained.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _inline_refs(schema, defs)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline_refs(defs[ref.removeprefix("#/$defs/")], defs)
        return {key: _inline_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def parse_verdict(text: str) -> ParsedVerdict:
    """Turn a critic's raw reply into a validated verdict."""
    verdict, extracted = parse_reply(text, CriticVerdict)
    return ParsedVerdict(verdict, extracted)


def parse_reply[M: BaseModel](text: str, model: type[M]) -> tuple[M, bool]:
    """Validate a raw model reply against a pydantic model.

    Returns the validated object and whether the JSON had to be cut out of
    surrounding text.
    """
    extracted = False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = _extract_json_object(text)
        extracted = True
    try:
        return model.model_validate(data), extracted
    except ValidationError as error:
        raise VerdictParseError(
            f"reply does not match the {model.__name__} schema: {error}"
        ) from error


def _extract_json_object(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VerdictParseError("reply contains no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise VerdictParseError(f"reply contains invalid JSON: {error}") from error

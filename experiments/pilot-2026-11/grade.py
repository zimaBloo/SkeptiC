"""Grade every pilot output against its answer key, blind, with two graders.

    uv run python experiments/pilot-2026-11/grade.py

Each grader sees the idea, the answer key and one review, never which setup
wrote it. It marks each listed weakness as found, partly found or not found,
whether the review presents it as a serious problem, and whether the review
says the idea cannot work at all. Grades go to out/grades/<grader>/<setup>/.
"""

import asyncio
import glob
import json
import sys
from pathlib import Path
from typing import Any, Literal

from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    ResponseSchema,
    get_model,
)
from inspect_ai.util import JSONSchema
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skeptic.lineups import load_env  # noqa: E402
from skeptic.single import SingleResult  # noqa: E402
from skeptic.verdict import json_schema_for  # noqa: E402

GRADERS = {
    "terra": ("openrouter/openai/gpt-6.1-terra", {"only": ["openai"], "allow_fallbacks": False}),
    "hydrogen": (
        "openrouter/google/gemini-4-hydrogen",
        {"only": ["google-ai-studio"], "allow_fallbacks": False},
    ),
}

INSTRUCTIONS = """You grade one written review of an innovation idea against an answer key.
You do not know who or what wrote the review; judge only what it says.

For each weakness in the answer key, decide:
- found: "yes" if the review clearly identifies this weakness (any wording),
  "partly" if it touches it but misses its core, "no" if it does not.
- serious: true if the review presents it as one of the serious problems
  (fatal, major, or among the main problems it leads with), false otherwise.
- evidence: the review's own words that show it, copied briefly, or "".

Then decide says_unworkable: true if the review says or clearly implies that
the idea cannot work in any form close to what is described (it calls a
problem fatal, the idea not viable, or says it must become a different
product or business). Give the problem it rests on in unworkable_reason, or "".

Be strict and literal. A generic remark does not count as finding a specific
weakness. Answer as JSON."""


class WeaknessGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    found: Literal["yes", "partly", "no"]
    serious: bool
    evidence: str


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weaknesses: list[WeaknessGrade] = Field(description="One entry per weakness in the key.")
    says_unworkable: bool
    unworkable_reason: str


def read_grade(text: str) -> Grade:
    """Read a grader's reply leniently: code fences, and a weakness number that is
    missing or named 'id', are accepted. Weaknesses are matched by their position,
    which is the key's order."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error
    weaknesses = []
    for position, entry in enumerate(data.get("weaknesses") or [], 1):
        found = str(entry.get("found", "no")).strip().lower()
        weaknesses.append(
            {
                "number": position,
                "found": found if found in ("yes", "partly", "no") else "no",
                "serious": bool(entry.get("serious", False)),
                "evidence": str(entry.get("evidence") or ""),
            }
        )
    return Grade.model_validate(
        {
            "weaknesses": weaknesses,
            "says_unworkable": bool(data.get("says_unworkable", False)),
            "unworkable_reason": str(data.get("unworkable_reason") or ""),
        }
    )


def panel_review(path: Path) -> tuple[str, dict[str, Any]]:
    record = json.loads(path.read_text(encoding="utf-8"))
    combined = record["combiner"]["combined"]
    if not combined:
        return "", {"error": record["combiner"]["error"] or record.get("error")}
    lines = ["Statement:", combined["statement"], "", "Points:"]
    lines += [f"- {p['point']} (severity: {p['severity']})" for p in combined["points"]]
    lines += ["", "Improvements:"] + [f"- {i}" for i in combined["improvements"]]
    quotes = sum(len(c["verdict"]["issues"]) for c in record["critics"] if c["verdict"])
    off = sum(len(c["checks"]["quotes_not_in_idea"]) for c in record["critics"] if c["checks"])
    return "\n".join(lines), {"quotes": quotes, "quotes_off": off, "log": record["log_file"]}


def single_review(log_dir: Path) -> tuple[str, dict[str, Any]]:
    [path] = glob.glob(str(log_dir / "*.eval"))
    sample = read_eval_log(path).samples[0]  # type: ignore[index]
    result = sample.store_as(SingleResult)
    if result.style == "plain":
        return sample.output.completion, {"log": Path(path).name}
    if result.verdict is None:
        return "", {"error": result.error, "log": Path(path).name}
    v = result.verdict
    lines = ["Statement:", v.statement, "", f"Overall: {v.overall}", "", "Issues:"]
    lines += [
        f"- ({i.severity}) {i.issue} {i.why_it_matters} Fix: {i.how_to_improve}" for i in v.issues
    ]
    off = len(result.checks.quotes_not_in_idea) if result.checks else 0
    return "\n".join(lines), {"quotes": len(v.issues), "quotes_off": off, "log": Path(path).name}


def outputs() -> list[dict[str, Any]]:
    found = []
    for path in sorted((HERE / "out" / "panel").glob("*.json")):
        text_id, rep = path.stem.rsplit(".r", 1)
        review, info = panel_review(path)
        found.append({"setup": "panel", "text": text_id, "rep": int(rep), "review": review, **info})
    for setup in ("plain", "checklist"):
        for log_dir in sorted((HERE / "logs" / setup).glob("*.r*")):
            text_id, rep = log_dir.name.rsplit(".r", 1)
            review, info = single_review(log_dir)
            found.append(
                {"setup": setup, "text": text_id, "rep": int(rep), "review": review, **info}
            )
    return found


async def grade_one(grader: str, item: dict[str, Any], semaphore: asyncio.Semaphore) -> None:
    out = HERE / "out" / "grades" / grader / item["setup"] / f"{item['text']}.r{item['rep']}.json"
    if out.exists() or not item["review"]:
        return
    model_name, provider = GRADERS[grader]
    model = get_model(model_name, memoize=True)
    idea = (HERE / "ideas" / f"{item['text']}.md").read_text(encoding="utf-8")
    key = (HERE / "ideas" / f"{item['text']}.key.md").read_text(encoding="utf-8")
    prompt = f"IDEA:\n{idea}\n\nANSWER KEY:\n{key}\n\nREVIEW TO GRADE:\n{item['review']}"
    config = GenerateConfig(
        max_tokens=8000,
        reasoning_effort="low",
        extra_body={"provider": provider},
        response_schema=ResponseSchema(
            name="grade", json_schema=JSONSchema.model_validate(json_schema_for(Grade)), strict=True
        ),
    )
    messages = [ChatMessageSystem(content=INSTRUCTIONS), ChatMessageUser(content=prompt)]
    async with semaphore:
        output = await model.generate(messages, config=config)
    try:
        grade = read_grade(output.completion)
    except ValueError as error:
        # Not every provider holds the grader to the schema; ask once more.
        async with semaphore:
            retry = [
                *messages,
                ChatMessageAssistant(content=output.completion),
                ChatMessageUser(
                    content=f"Your reply could not be read ({error}). Send it again as one JSON "
                    "object with weaknesses (each with number, found, serious, evidence), "
                    "says_unworkable and unworkable_reason, and nothing else. Inside text use "
                    "single quotes, never double quotes."
                ),
            ]
            output = await model.generate(retry, config=config)
        grade = read_grade(output.completion)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(grade.model_dump_json(indent=1), encoding="utf-8")


async def main() -> None:
    load_env(ROOT)
    items = outputs()
    (HERE / "out" / "outputs.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    semaphore = asyncio.Semaphore(6)
    tasks = [grade_one(g, item, semaphore) for g in GRADERS for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    print(f"graded {len(tasks) - len(failures)} of {len(tasks)}; failures: {len(failures)}")
    for failure in failures[:5]:
        print("  ", repr(failure)[:300])


if __name__ == "__main__":
    asyncio.run(main())

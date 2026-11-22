"""Turn the pilot's grades into tables: results.md and results.json.

    uv run python experiments/pilot-2026-11/analyze.py

Texts by kind (from their file names and keys):
    <id>-flaw    a twin with one planted fatal flaw (key weakness 1)
    <id>-sound   the same idea without it; no fatal issue expected
    pumpwatch    a sound control idea; no fatal issue expected
    shelfsense, lunchloop, shiftswap   several planted weaknesses each
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from inspect_ai.event import ModelEvent
from inspect_ai.log import read_eval_log

HERE = Path(__file__).resolve().parent
SETUPS = ("panel", "checklist", "plain")
NAMES = {
    "panel": "Panel (4 critics + combiner)",
    "checklist": "One model, same checklist",
    "plain": "One model, plain question",
}
MULTI = ("shelfsense", "lunchloop", "shiftswap")
# The independent check rejected these twins (flaw not clearly fatal, signposted, or
# the twin changed more than the flaw). Their sound versions passed and still count.
REJECTED_TWINS = ("meddrop", "balconysun", "rideledger")
SOUND_CONTROLS = ("pumpwatch",)


def load_grades() -> dict[tuple[str, str, str, int], dict[str, Any]]:
    grades = {}
    for path in (HERE / "out" / "grades").glob("*/*/*.json"):
        grader, setup = path.parts[-3], path.parts[-2]
        text, rep = path.stem.rsplit(".r", 1)
        grades[(grader, setup, text, int(rep))] = json.loads(path.read_text(encoding="utf-8"))
    return grades


def cost_and_time(item: dict[str, Any]) -> tuple[float | None, float | None]:
    setup, log_name = item["setup"], item.get("log")
    if not log_name:
        return None, None
    if setup == "panel":
        path = HERE / "logs" / "panel" / log_name
    else:
        path = HERE / "logs" / setup / f"{item['text']}.r{item['rep']}" / log_name
    log = read_eval_log(str(path))
    sample = log.samples[0]  # type: ignore[index]
    cost = 0.0
    for event in sample.events:
        if isinstance(event, ModelEvent) and event.call and event.call.response:
            cost += float((event.call.response.get("usage") or {}).get("cost") or 0.0)
    seconds = sample.total_time
    return cost, seconds


def rate(values: list[bool]) -> str:
    if not values:
        return "n/a"
    return f"{sum(values)}/{len(values)} ({100 * sum(values) / len(values):.0f}%)"


def main() -> int:
    items = json.loads((HERE / "out" / "outputs.json").read_text(encoding="utf-8"))
    grades = load_grades()
    graders = sorted({g for g, *_ in grades})
    by_setup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_setup[item["setup"]].append(item)

    table: dict[str, dict[str, str]] = {}
    raw: dict[str, Any] = {"graders": graders, "setups": {}}
    agreement: list[bool] = []
    for setup in SETUPS:
        twin_found, twin_fatal, sound_fatal, multi_yes, multi_any, multi_serious = (
            [] for _ in range(6)
        )
        all_twin_found, all_twin_fatal = [], []
        costs, times, quotes, quotes_off = [], [], 0, 0
        for item in by_setup[setup]:
            text, rep = item["text"], item["rep"]
            cost, seconds = cost_and_time(item)
            if cost is not None:
                costs.append(cost)
            if seconds is not None:
                times.append(seconds)
            quotes += item.get("quotes", 0)
            quotes_off += item.get("quotes_off", 0)
            per_grader = [grades.get((g, setup, text, rep)) for g in graders]
            per_grader = [g for g in per_grader if g]
            if len(per_grader) == 2:
                a, b = per_grader
                for wa, wb in zip(a["weaknesses"], b["weaknesses"], strict=False):
                    agreement.append(wa["found"] == wb["found"])
                agreement.append(a["says_unworkable"] == b["says_unworkable"])
            for grade in per_grader:
                weaknesses = grade["weaknesses"]
                if text.endswith("-flaw") and weaknesses:
                    hit = weaknesses[0]["found"] == "yes"
                    fatal = hit and grade["says_unworkable"]
                    all_twin_found.append(hit)
                    all_twin_fatal.append(fatal)
                    if text.removesuffix("-flaw") not in REJECTED_TWINS:
                        twin_found.append(hit)
                        twin_fatal.append(fatal)
                if text.endswith("-sound") or text in SOUND_CONTROLS:
                    sound_fatal.append(bool(grade["says_unworkable"]))
                if text in MULTI:
                    for w in weaknesses:
                        multi_yes.append(w["found"] == "yes")
                        multi_any.append(w["found"] in ("yes", "partly"))
                        multi_serious.append(w["found"] == "yes" and w["serious"])
        table[setup] = {
            "Planted fatal flaw found (3 checked twins)": rate(twin_found),
            "...and called unworkable": rate(twin_fatal),
            "Same, all 6 twins (3 failed the check)": rate(all_twin_found),
            "...and called unworkable (all 6)": rate(all_twin_fatal),
            "Sound idea called unworkable (false alarm, 7 ideas)": rate(sound_fatal),
            "Planted weaknesses found (3 ideas, 13)": rate(multi_yes),
            "...found at least partly": rate(multi_any),
            "...found and called serious": rate(multi_serious),
            "Quotes not in the idea": f"{quotes_off}/{quotes}" if quotes else "n/a (free text)",
            "Cost per verdict (mean)": f"${statistics.mean(costs):.3f}" if costs else "n/a",
            "Time per verdict (mean)": f"{statistics.mean(times):.0f} s" if times else "n/a",
        }
        raw["setups"][setup] = {
            "twin_found": twin_found,
            "twin_fatal": twin_fatal,
            "sound_fatal": sound_fatal,
            "multi_yes": multi_yes,
            "costs": costs,
            "times": times,
            "quotes": quotes,
            "quotes_off": quotes_off,
        }

    rows = list(next(iter(table.values())).keys())
    lines = [
        "# Pilot: panel against one model (22 Nov 2026)",
        "",
        "Rates count each grade separately: every output was graded by "
        f"{len(graders)} graders ({', '.join(graders)}), so n is outputs x graders.",
        f"Grader agreement on individual labels: {rate(agreement)}.",
        "",
        "| Measure | " + " | ".join(NAMES[s] for s in SETUPS) + " |",
        "|---|" + "---|" * len(SETUPS),
    ]
    for row in rows:
        lines.append(f"| {row} | " + " | ".join(table[s][row] for s in SETUPS) + " |")
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw["agreement"] = agreement
    (HERE / "results.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

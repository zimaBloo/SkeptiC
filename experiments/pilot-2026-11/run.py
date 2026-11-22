"""Run the panel and the two single-judge baselines on the pilot's idea texts.

    uv run python experiments/pilot-2026-11/run.py TEXT_ID [TEXT_ID ...] [--rep N]

Each text runs in three setups:
    panel      the SkeptiC frontier lineup (skeptic run --lineup frontier)
    plain      one model, one plain question (skeptic/single_judge, style=plain)
    checklist  one model, the panel's checklist (skeptic/single_judge, style=checklist)

Runs go a few at a time. A run whose output already exists is skipped, so the
script can be started again after a stop. It stops starting new runs once the
OpenRouter key has spent more than BUDGET dollars in all.
"""

import argparse
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUDGET = 8.0  # dollars spent on the key in all, the pilot included
SETUPS = ("panel", "plain", "checklist")
lock = threading.Lock()


def spent() -> float:
    key = dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY") or ""
    reply = httpx.get(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20
    )
    return float(reply.json()["data"]["usage"])


def command(setup: str, text_id: str, rep: int) -> tuple[list[str], Path]:
    idea = HERE / "ideas" / f"{text_id}.md"
    name = f"{text_id}.r{rep}"
    logs = HERE / "logs" / setup
    if setup == "panel":
        out = HERE / "out" / "panel" / f"{name}.json"
        return [
            "uv",
            "run",
            "skeptic",
            "run",
            str(idea),
            "--lineup",
            "frontier",
            "--idea-id",
            name,
            "--out",
            str(out),
            "--log-dir",
            str(logs),
        ], out
    out = HERE / "out" / setup / f"{name}.done"
    return [
        "uv",
        "run",
        "inspect",
        "eval",
        "skeptic/single_judge",
        "-T",
        f"idea={idea}",
        "-T",
        f"idea_id={name}",
        "-T",
        f"style={setup}",
        "--log-dir",
        str(logs / name),
        "--display",
        "plain",
    ], out


def run(setup: str, text_id: str, rep: int) -> str:
    cmd, out = command(setup, text_id, rep)
    if out.exists():
        return f"skip {setup} {text_id} r{rep} (done)"
    with lock:
        used = spent()
    if used > BUDGET:
        return f"STOP {setup} {text_id} r{rep}: ${used:.2f} spent, over the ${BUDGET} budget"
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    seconds = time.time() - started
    (out.parent / f"{text_id}.r{rep}.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    if setup != "panel" and done.returncode == 0:
        out.write_text(f"{seconds:.1f}\n", encoding="utf-8")
    status = "ok" if done.returncode == 0 else f"FAILED ({done.returncode})"
    return f"{status} {setup} {text_id} r{rep} in {seconds:.0f}s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("texts", nargs="+")
    parser.add_argument("--rep", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    jobs = [(s, t, args.rep) for t in args.texts for s in SETUPS]
    print(f"spent before: ${spent():.4f}", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        for line in pool.map(lambda job: run(*job), jobs):
            print(line, flush=True)
    print(f"spent after: ${spent():.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

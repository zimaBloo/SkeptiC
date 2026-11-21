"""Shared helpers: run the panel offline, with a mock model for each role."""

import json
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.model import ChatMessage, GenerateConfig, ModelOutput, get_model
from inspect_ai.tool import ToolChoice, ToolInfo

from skeptic.critics import CRITICS
from skeptic.panel import skeptic_panel

IDEA = "A fridge camera. There are no direct competitors."

COMBINED = json.dumps(
    {
        "points": [
            {
                "point": "Competitors exist.",
                "critics": ["market", "technical", "customer", "risk"],
                "severity": "major",
            }
        ],
        "disagreements": [],
        "improvements": ["Name the competitors."],
        "statement": "The panel's statement.",
    }
)


def verdict(
    critic: str,
    quote: str = "There are no direct competitors",
    severity: str = "major",
    fixable: bool = True,
) -> str:
    return json.dumps(
        {
            "issues": [
                {
                    "quote": quote,
                    "issue": f"A {critic} problem.",
                    "why_it_matters": "It weakens the plan.",
                    "how_to_improve": "Fix the claim.",
                    "fixable_within_idea": fixable,
                    "severity": severity,
                }
            ],
            "summary": f"The {critic} critic's summary.",
            "overall": "promising",
        }
    )


class Recorder:
    """A mock model that gives a fixed reply and records every request it gets."""

    def __init__(self, reply: str, stop_reason: Any = "stop") -> None:
        self.reply = reply
        self.stop_reason = stop_reason
        self.calls: list[tuple[list[ChatMessage], GenerateConfig]] = []

    def __call__(
        self,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        self.calls.append((input, config))
        return ModelOutput.from_content(
            model="mockllm/model", content=self.reply, stop_reason=self.stop_reason
        )


def run_panel(
    tmp_path: Path, task_args: dict[str, Any] | None = None, **replies: str | Recorder
) -> tuple[EvalLog, dict[str, Recorder]]:
    """Run the panel with a mock model per role. Unnamed roles give a valid reply."""
    defaults: dict[str, str | Recorder] = {critic: verdict(critic) for critic in CRITICS}
    recorders = {
        role: reply if isinstance(reply, Recorder) else Recorder(reply)
        for role, reply in (defaults | {"combiner": COMBINED} | replies).items()
    }
    idea_file = tmp_path / "idea.md"
    idea_file.write_text(IDEA, encoding="utf-8")
    task: Task = skeptic_panel(idea=str(idea_file), **(task_args or {}))

    [log] = inspect_eval(
        task,
        model_roles={
            role: get_model("mockllm/model", custom_outputs=recorder, memoize=False)
            for role, recorder in recorders.items()
        },
        display="none",
        log_dir=str(tmp_path / "logs"),
    )
    return log, recorders


def only_sample(log: EvalLog) -> EvalSample:
    assert log.samples is not None
    [sample] = log.samples
    return sample

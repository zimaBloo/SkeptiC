"""The model lineups the panel can run on.

A lineup puts a model on each critic and on the combiner, and sets how much
the models reason and how long a reply may be.

- local: small models on this computer's Ollama server. The idea never leaves
  the machine, and with temperature 0 and a fixed seed a run can be repeated.
- frontier: hosted models from four labs, reached through OpenRouter with one
  key and one prepaid balance. Much stronger, but the idea goes to the labs'
  servers, and runs vary: only Grok uses the temperature setting (Claude, GPT
  and Gemini sample at a fixed temperature), and Claude takes no seed.

Inspect's --model-role (or `skeptic run --role`) still swaps a single role.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from dotenv import dotenv_values

from skeptic.critics import CRITICS

LineupName = Literal["local", "frontier"]
LINEUP_NAMES: tuple[LineupName, ...] = get_args(LineupName)

Reasoning = Literal["none", "low", "medium", "high", "default"]
"""How much a model may reason before it answers. "default" sends no setting, so
each model does what it does by default (Ollama models that can think, think)."""
REASONING_LEVELS: tuple[Reasoning, ...] = get_args(Reasoning)

ROLES: tuple[str, ...] = (*CRITICS, "combiner")


@dataclass(frozen=True)
class Lineup:
    name: LineupName
    title: str
    description: str
    models: Mapping[str, str]
    reasoning: Reasoning
    max_tokens: int
    key: str | None = None
    """The environment variable that must hold an API key, if the lineup needs one."""
    always_reasons: bool = False
    """The models cannot turn reasoning off, so reasoning "none" is refused."""


@dataclass(frozen=True)
class Route:
    """Where OpenRouter may send a model's calls, and the most it may charge.

    Pinned to the lab's own endpoint: other hosts of the same model do not all
    support structured outputs (Claude on Amazon Bedrock does not). The price
    cap, in dollars per million tokens, is at or just above the model's list
    price. If a lab raises the price above the cap, that model's calls fail
    until the cap here is raised: a critic gives no answer and the others go
    on, while a failed combiner fails the run.
    """

    provider: str
    prompt: float
    completion: float

    def provider_options(self) -> dict[str, object]:
        return {
            "only": [self.provider],
            "allow_fallbacks": False,
            "max_price": {"prompt": self.prompt, "completion": self.completion},
        }


# One model family per critic, chosen from a test of four local models on the
# demo idea (21 Nov 2026). The combiner runs on the model that made the fewest
# factual errors in that test.
LOCAL = Lineup(
    name="local",
    title="Local",
    description="On this computer. The idea stays here; a run takes several minutes.",
    models={
        "market": "ollama/qwen3.5:9b-q4_K_M",
        "technical": "ollama/gemma4:12b-it-qat",
        "customer": "ollama/ministral-3:8b-instruct-2512-q4_K_M",
        "risk": "ollama/nemotron-3-nano:4b-q4_K_M",
        "combiner": "ollama/gemma4:12b-it-qat",
    },
    # The local models were tested with thinking off.
    reasoning="none",
    max_tokens=3000,
)

# One lab per critic (22 Nov 2026). These models always reason, so the level is
# the lowest they all accept, and replies get room for the reasoning tokens,
# which count against max_tokens.
FRONTIER = Lineup(
    name="frontier",
    title="Frontier",
    description="Four labs, through OpenRouter. The idea is sent to them; a run takes a minute "
    "or two.",
    models={
        "market": "openrouter/openai/gpt-6.2-sol",
        "technical": "openrouter/google/gemini-4-carbon",
        "customer": "openrouter/anthropic/claude-sonnet-5.5",
        "risk": "openrouter/x-ai/grok-4.7",
        "combiner": "openrouter/anthropic/claude-opus-5.5",
    },
    reasoning="low",
    max_tokens=16000,
    key="OPENROUTER_API_KEY",
    always_reasons=True,
)

LINEUPS: dict[LineupName, Lineup] = {lineup.name: lineup for lineup in (LOCAL, FRONTIER)}

# List prices on OpenRouter, 22 Nov 2026.
ROUTES: dict[str, Route] = {
    "openrouter/openai/gpt-6.2-sol": Route("openai", 2, 10),
    # $2/$10 until Google raises it to $4/$20 on 1 Dec 2026; the cap already
    # allows the new price.
    "openrouter/google/gemini-4-carbon": Route("google-ai-studio", 4, 20),
    "openrouter/anthropic/claude-sonnet-5.5": Route("anthropic", 2, 10),
    "openrouter/x-ai/grok-4.7": Route("xai", 2, 6),
    "openrouter/anthropic/claude-opus-5.5": Route("anthropic", 4, 20),
    # Small models from two of the same labs, for trying a key and the routing
    # for a fraction of a cent (skeptic run --lineup frontier --role ...).
    "openrouter/openai/gpt-6-luna": Route("openai", 0.1, 0.5),
    "openrouter/anthropic/claude-haiku-5.5": Route("anthropic", 0.1, 0.5),
}


def check_reasoning(lineup: Lineup, level: str) -> None:
    """Refuse a reasoning level the lineup's models do not accept."""
    if level not in REASONING_LEVELS:
        raise ValueError(f"reasoning must be one of {', '.join(REASONING_LEVELS)}, not {level!r}")
    if level == "none" and lineup.always_reasons:
        raise ValueError(
            f"the {lineup.name} models always reason: use low, medium, high or default, not none"
        )


def get_lineup(name: str) -> Lineup:
    for lineup in LINEUPS.values():
        if lineup.name == name:
            return lineup
    raise ValueError(f"no lineup called {name!r} (there are: {', '.join(LINEUP_NAMES)})")


def env_file(folder: Path) -> Path | None:
    """The .env file a run started in folder reads: the first one in it or above it."""
    for candidate in [folder, *folder.parents]:
        path = candidate / ".env"
        if path.is_file():
            return path
    return None


def load_env(folder: Path) -> None:
    """Put the variables of folder's .env into the environment.

    A variable that is already set keeps its value, as with Inspect's own
    loading, but one that is set and empty takes the value from .env.
    """
    path = env_file(folder)
    if path is None:
        return
    for name, value in dotenv_values(path).items():
        if value and not os.environ.get(name):
            os.environ[name] = value


def missing_key(lineup: Lineup, folder: Path | None = None) -> str | None:
    """The environment variable the lineup needs and does not have, or None.

    The variable counts as there when it is set and not empty, in the
    environment or in the .env file that a run started in folder reads.
    """
    if lineup.key is None or os.environ.get(lineup.key):
        return None
    path = env_file(folder) if folder is not None else None
    if path is not None and dotenv_values(path).get(lineup.key):
        return None
    return lineup.key

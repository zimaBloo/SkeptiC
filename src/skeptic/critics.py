"""The four critics and the prompts they get.

The prompt text lives in the prompts/ folder next to this file, so it can be
read and reviewed on its own. This module only puts the pieces together.
"""

import hashlib
from collections.abc import Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from string import Template
from typing import Literal, get_args

CriticName = Literal["market", "technical", "customer", "risk"]
CRITICS: tuple[CriticName, ...] = get_args(CriticName)

TITLES: dict[CriticName, str] = {
    "market": "market",
    "technical": "technical feasibility",
    "customer": "customer value",
    "risk": "risk and regulation",
}


def load_prompt(name: str) -> str:
    """Read a prompt file from the prompts/ folder."""
    return files("skeptic").joinpath("prompts", name).read_text(encoding="utf-8")


def critic_system_prompt(critic: CriticName) -> str:
    """The system prompt for one critic: the shared rules plus its own focus."""
    return Template(load_prompt("critic.md")).substitute(
        title=TITLES[critic],
        focus=load_prompt(f"roles/{critic}.md").strip(),
    )


def submission_prompt(idea: str) -> str:
    """The user message every critic gets: the idea and nothing else."""
    return Template(load_prompt("submission.md")).substitute(idea=idea.strip())


def prompts_digest() -> str:
    """SHA-256 over every prompt file, so a run's log shows which prompt text it used."""
    digest = hashlib.sha256()
    for name, text in sorted(_prompt_files(files("skeptic").joinpath("prompts"), "")):
        digest.update(f"{name}\0{text}\0".encode())
    return digest.hexdigest()


def _prompt_files(folder: Traversable, prefix: str) -> Iterator[tuple[str, str]]:
    for entry in folder.iterdir():
        name = prefix + entry.name
        if entry.is_dir():
            yield from _prompt_files(entry, name + "/")
        elif entry.name.endswith(".md"):
            yield name, entry.read_text(encoding="utf-8")

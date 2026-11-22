"""A local web API for the web UI: the sample ideas, the model lineups, and panel
runs with their progress.

Each run is a `skeptic run` process of its own. Only one runs at a time: the
local models share one Ollama server, and a demo follows one run anyway. A run
keeps its files in runs/<run_id>/:

    idea.md         the idea the critics read
    lineup          the name of the lineup it runs on (older runs: local)
    progress.jsonl  the panel's progress events (see skeptic.progress)
    record.json     the run record, once the run has ended
    process.log     what the process printed
    stopped         the time the run was stopped, if it was

Start it with `uv run skeptic serve`. When web/dist/index.html exists, the
built UI is served at / as well.
"""

import os
import re
import subprocess
import sys
import threading
import unicodedata
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from skeptic.lineups import LINEUPS, LineupName, get_lineup, missing_key
from skeptic.progress import ProgressEvent, read_events
from skeptic.record import PanelRecord

SLUG = r"^[a-z0-9][a-z0-9_-]{0,63}$"
RUN_ID = r"^[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9_-]{0,79}$"
ANSWER_KEYS = (".planted-flaws.md", ".expected.md")
STOP_TIMEOUT_SECONDS = 10.0
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

RunStatus = Literal["running", "success", "error", "stopped"]
Slug = Annotated[str, StringConstraints(pattern=SLUG)]
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\r\n]+$"),
]
IdeaText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=200, max_length=20_000)
]


class Health(BaseModel):
    ok: bool = True


class IdeaSummary(BaseModel):
    id: str
    title: str
    chars: int = Field(description="Length of the idea's text in characters.")
    sample: bool = Field(default=True, description="A made-up idea from the ideas/ folder.")


class Idea(BaseModel):
    id: str
    title: str
    text: str


class LineupInfo(BaseModel):
    name: LineupName
    title: str
    description: str
    models: dict[str, str] = Field(description="The model of every role, the combiner included.")
    ready: bool = Field(description="The lineup can run now: any API key it needs is set.")
    missing_key: str | None = Field(
        description="The environment variable to set in .env before the lineup can run."
    )


class RunRequest(BaseModel):
    """A run on a sample idea (idea_id), or on pasted text (title and text)."""

    model_config = ConfigDict(extra="forbid")

    idea_id: Slug | None = None
    title: Title | None = None
    text: IdeaText | None = None
    lineup: LineupName = "local"

    @model_validator(mode="after")
    def check_source(self) -> Self:
        if self.idea_id is not None and (self.title is not None or self.text is not None):
            raise ValueError("give either idea_id, or title and text, not both")
        if self.idea_id is None and (self.title is None or self.text is None):
            raise ValueError("give either idea_id, or both title and text")
        return self


class RunCreated(BaseModel):
    id: str


class RunSummary(BaseModel):
    id: str
    idea_title: str
    lineup: LineupName
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None


class RunDetail(RunSummary):
    idea_text: str
    events: list[ProgressEvent]
    record: PanelRecord | None = Field(description="The run record, once the run has one.")
    error: str | None


class RunProcess(Protocol):
    """The part of subprocess.Popen the server uses."""

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class Launcher(Protocol):
    def __call__(self, command: list[str], cwd: Path, output: Path) -> RunProcess: ...


def start_process(command: list[str], cwd: Path, output: Path) -> RunProcess:
    """Start a run's process, with everything it prints going to the output file."""
    with output.open("wb") as file:
        return subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=file,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )


@dataclass(frozen=True)
class _State:
    status: RunStatus
    error: str | None = None
    record: PanelRecord | None = None
    finished_at: datetime | None = None


class Runs:
    """The runs under root/runs, and the processes this server started for them."""

    def __init__(self, root: Path, launcher: Launcher) -> None:
        self.root = root
        self.folder = root / "runs"
        self.launcher = launcher
        self.processes: dict[str, RunProcess] = {}
        self.lock = threading.Lock()

    def start(self, slug: str, idea: str, lineup: LineupName = "local") -> str:
        """Start a run on the idea text and return its id."""
        with self.lock:
            active = self.active()
            if active is not None:
                raise HTTPException(409, f"run {active} is still going; one run at a time")
            run_id, folder = self._new_folder(slug)
            (folder / "idea.md").write_text(idea, encoding="utf-8")
            (folder / "lineup").write_text(lineup, encoding="utf-8")
            files = folder.relative_to(self.root)
            command = [
                sys.executable,
                "-m",
                "skeptic.cli",
                "run",
                str(files / "idea.md"),
                "--out",
                str(files / "record.json"),
                "--progress",
                str(files / "progress.jsonl"),
                "--idea-id",
                slug,
                "--lineup",
                lineup,
                "--log-dir",
                "logs",
            ]
            self.processes[run_id] = self.launcher(command, self.root, folder / "process.log")
            return run_id

    def stop(self, run_id: str) -> None:
        """Terminate a run's process and mark the run as stopped."""
        with self.lock:
            folder = self._folder(run_id)
            process = self.processes.get(run_id)
            if process is None or process.poll() is not None:
                raise HTTPException(409, f"run {run_id} is not running")
            (folder / "stopped").write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
            process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def shutdown(self) -> None:
        """Terminate the runs still going; after a restart they show as interrupted."""
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()

    def active(self) -> str | None:
        return next((i for i, process in self.processes.items() if process.poll() is None), None)

    def summaries(self) -> list[RunSummary]:
        """Every run, newest first."""
        if not self.folder.is_dir():
            return []
        ids = [
            folder.name
            for folder in self.folder.iterdir()
            if re.fullmatch(RUN_ID, folder.name) and (folder / "idea.md").is_file()
        ]
        return [self.summary(run_id) for run_id in sorted(ids, reverse=True)]

    def summary(self, run_id: str) -> RunSummary:
        folder = self._folder(run_id)
        return self._summary(run_id, folder, self._state(run_id, folder))

    def detail(self, run_id: str) -> RunDetail:
        folder = self._folder(run_id)
        state = self._state(run_id, folder)
        return RunDetail(
            **self._summary(run_id, folder, state).model_dump(),
            idea_text=(folder / "idea.md").read_text(encoding="utf-8"),
            events=read_events(folder / "progress.jsonl"),
            record=state.record,
            error=state.error,
        )

    def _summary(self, run_id: str, folder: Path, state: _State) -> RunSummary:
        idea = (folder / "idea.md").read_text(encoding="utf-8")
        return RunSummary(
            id=run_id,
            idea_title=markdown_title(idea, run_id.split("-", 2)[2]),
            lineup=_read_lineup(folder / "lineup"),
            status=state.status,
            started_at=datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC),
            finished_at=state.finished_at,
        )

    def _state(self, run_id: str, folder: Path) -> _State:
        stopped = folder / "stopped"
        if stopped.is_file():
            return _State("stopped", finished_at=_read_time(stopped))
        process = self.processes.get(run_id)
        if process is not None and process.poll() is None:
            return _State("running")
        record_file = folder / "record.json"
        output = folder / "process.log"
        if record_file.is_file():
            try:
                record = PanelRecord.model_validate_json(record_file.read_bytes())
            except ValidationError as error:
                message = f"the run record cannot be read: {error}"
                return _State("error", message, finished_at=_modified(record_file))
            finished_at = record.finished_at or _modified(record_file)
            if record.status == "success":
                return _State("success", record=record, finished_at=finished_at)
            error = record.error or f"the run ended with status {record.status!r}"
            return _State("error", error, record, finished_at)
        if process is None:
            return _State("error", "interrupted", finished_at=_modified(output))
        return _State("error", _failure(process.poll(), output), finished_at=_modified(output))

    def _folder(self, run_id: str) -> Path:
        folder = self.folder / run_id
        if not (folder / "idea.md").is_file():
            raise HTTPException(404, f"no such run: {run_id}")
        return folder

    def _new_folder(self, slug: str) -> tuple[str, Path]:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.folder.mkdir(exist_ok=True)
        number = 1
        while True:
            run_id = f"{stamp}-{slug}" if number == 1 else f"{stamp}-{slug}-{number}"
            try:
                (self.folder / run_id).mkdir()
            except FileExistsError:
                number += 1
                continue
            return run_id, self.folder / run_id


class SameOriginWrites:
    """Refuses a request that can change something when a page on another origin sent it.

    Browsers send Origin with a cross-site POST, also with a no-cors one that CORS
    does not stop. curl and the CLI send no Origin and pass.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not _same_host(origin, headers.get("host")):
                refusal = JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    root: Path | None = None,
    launcher: Launcher = start_process,
    allowed_hosts: Sequence[str] = LOCAL_HOSTS,
) -> FastAPI:
    """The API, reading ideas/ and writing runs/ under root (by default the current folder).

    It answers only requests sent to one of allowed_hosts, which shuts out a page
    that reaches it through DNS rebinding. Tests pass their own launcher, so that
    no real panel process starts.
    """
    root = (root or Path.cwd()).resolve()
    ideas = root / "ideas"
    web = root / "web" / "dist"
    runs = Runs(root, launcher)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        runs.shutdown()

    app = FastAPI(title="SkeptiC", lifespan=lifespan)
    app.add_middleware(SameOriginWrites)
    # Added last, so it runs first.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.get("/api/health")
    def health() -> Health:
        return Health()

    @app.get("/api/ideas")
    def list_ideas() -> list[IdeaSummary]:
        return sample_ideas(ideas)

    @app.get("/api/ideas/{idea_id}")
    def get_idea(idea_id: Annotated[str, PathParam(pattern=SLUG)]) -> Idea:
        return sample_idea(ideas, idea_id)

    @app.get("/api/lineups")
    def list_lineups() -> list[LineupInfo]:
        infos = []
        for lineup in LINEUPS.values():
            key = missing_key(lineup, root)
            infos.append(
                LineupInfo(
                    name=lineup.name,
                    title=lineup.title,
                    description=lineup.description,
                    models=dict(lineup.models),
                    ready=key is None,
                    missing_key=key,
                )
            )
        return infos

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest) -> RunCreated:
        key = missing_key(get_lineup(request.lineup), root)
        if key is not None:
            raise HTTPException(
                400, f"the {request.lineup} lineup needs an API key: put {key}=... in .env"
            )
        if request.idea_id is not None:
            idea = sample_idea(ideas, request.idea_id)
            return RunCreated(id=runs.start(idea.id, idea.text, request.lineup))
        assert request.title is not None and request.text is not None
        text = f"# {request.title}\n\n{request.text}\n"
        return RunCreated(id=runs.start(slugify(request.title), text, request.lineup))

    @app.get("/api/runs")
    def list_runs() -> list[RunSummary]:
        return runs.summaries()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: Annotated[str, PathParam(pattern=RUN_ID)]) -> RunDetail:
        return runs.detail(run_id)

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: Annotated[str, PathParam(pattern=RUN_ID)]) -> RunSummary:
        runs.stop(run_id)
        return runs.summary(run_id)

    @app.get("/{path:path}", include_in_schema=False)
    def web_ui(path: str) -> FileResponse:
        base = web.resolve()
        index = base / "index.html"
        if path == "api" or path.startswith("api/") or not index.is_file():
            raise HTTPException(404, "Not Found")
        file = (base / path).resolve()
        if file.is_file() and file.is_relative_to(base):
            return FileResponse(file)
        return FileResponse(index)

    return app


def serve_hosts(address: str, extra: Sequence[str] = ()) -> list[str]:
    """The hosts the API answers when it listens on the address, plus any extra names.

    A wildcard address (0.0.0.0, ::) is not a name a browser sends, so it adds
    nothing: other machines need their name for this one passed in extra.
    """
    hosts: list[str] = list(LOCAL_HOSTS)
    for name in [address, *extra]:
        host = f"[{name}]" if ":" in name and not name.startswith("[") else name
        if host.lower() not in hosts and host not in ("0.0.0.0", "[::]"):
            hosts.append(host)
    return hosts


def sample_ideas(folder: Path) -> list[IdeaSummary]:
    """The sample ideas in a folder. Answer keys are never listed."""
    ideas = []
    for path in sorted(folder.glob("*.md")):
        if _is_sample(path):
            text = path.read_text(encoding="utf-8")
            title = markdown_title(text, path.stem)
            ideas.append(IdeaSummary(id=path.stem, title=title, chars=len(text)))
    return ideas


def sample_idea(folder: Path, idea_id: str) -> Idea:
    path = folder / f"{idea_id}.md"
    if not re.fullmatch(SLUG, idea_id) or not _is_sample(path):
        raise HTTPException(404, f"no such idea: {idea_id}")
    text = path.read_text(encoding="utf-8")
    return Idea(id=idea_id, title=markdown_title(text, idea_id), text=text)


def markdown_title(text: str, fallback: str) -> str:
    """The first Markdown heading's text, or the fallback when there is none."""
    match = re.search(r"^#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def slugify(title: str) -> str:
    """A short form of a title for a run id, e.g. 'shelfsense-a-fridge-camera'."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    return slug[:40].rstrip("-") or "idea"


def _is_sample(path: Path) -> bool:
    return (
        not path.name.endswith(ANSWER_KEYS)
        and re.fullmatch(SLUG, path.stem) is not None
        and path.is_file()
    )


def _same_host(origin: str, host: str | None) -> bool:
    """Whether an Origin header names the host and port the request was sent to."""
    _, separator, authority = origin.partition("://")
    return bool(separator) and host is not None and authority.lower() == host.lower()


def _failure(code: int | None, output: Path) -> str:
    """Why a process ended without a record, from the last lines it printed."""
    reason = f"the run process ended with exit code {code} and wrote no record"
    lines = _last_lines(output, 3)
    return f"{reason}:\n" + "\n".join(lines) if lines else reason


def _last_lines(path: Path, count: int) -> list[str]:
    try:
        with path.open("rb") as file:
            file.seek(0, os.SEEK_END)
            file.seek(max(0, file.tell() - 8192))
            text = file.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()][-count:]


def _modified(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _read_lineup(path: Path) -> LineupName:
    """The lineup a run used. Runs from before lineups existed ran on the local one."""
    try:
        name = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "local"
    return get_lineup(name).name if name in LINEUPS else "local"


def _read_time(path: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except OSError, ValueError:
        return _modified(path)

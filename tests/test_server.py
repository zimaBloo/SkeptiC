"""The web API, with a fake launcher in place of the panel process."""

import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from skeptic.critics import CRITICS
from skeptic.progress import CriticFinished, CriticStarted, ProgressWriter, RunStarted
from skeptic.record import CombinerRecord, PanelRecord
from skeptic.server import (
    LOCAL_HOSTS,
    create_app,
    markdown_title,
    serve_hosts,
    slugify,
    start_process,
)

# TestClient sends its requests to the host "testserver".
HOSTS = [*LOCAL_HOSTS, "testserver"]
SAMPLE = "# ShelfSense: a fridge camera\n\nA fridge camera. There are no direct competitors.\n"
PASTED = "A subscription for repairing bicycles at the workplace. " * 5


class FakeProcess:
    """Stands in for a panel process; the test decides when it ends."""

    def __init__(self, ignores_terminate: bool = False) -> None:
        self.returncode: int | None = None
        self.ignores_terminate = ignores_terminate
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("skeptic", timeout or 0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self.ignores_terminate:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, Path]] = []
        self.processes: list[FakeProcess] = []
        self.ignores_terminate = False

    def __call__(self, command: list[str], cwd: Path, output: Path) -> FakeProcess:
        self.calls.append((command, cwd, output))
        output.write_text("", encoding="utf-8")
        self.processes.append(FakeProcess(self.ignores_terminate))
        return self.processes[-1]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    ideas = tmp_path / "ideas"
    ideas.mkdir()
    (ideas / "shelfsense.md").write_text(SAMPLE, encoding="utf-8")
    (ideas / "shelfsense.planted-flaws.md").write_text("# Answer key\n\nSecret.", encoding="utf-8")
    (ideas / "pumpwatch.expected.md").write_text("# Answer key\n\nSecret.", encoding="utf-8")
    (ideas / "untitled.md").write_text("An idea without a heading.", encoding="utf-8")
    return tmp_path


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def client(root: Path, launcher: FakeLauncher) -> Iterator[TestClient]:
    with TestClient(create_app(root, launcher, HOSTS)) as client:
        yield client


def start(client: TestClient, body: dict[str, Any] | None = None) -> str:
    response = client.post("/api/runs", json=body or {"idea_id": "shelfsense"})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def record_json(status: str = "success", error: str | None = None) -> str:
    return PanelRecord(
        idea_id="idea",
        idea=SAMPLE,
        status=status,
        error=error,
        finished_at=datetime(2026, 11, 21, 23, 20, tzinfo=UTC),
        log_file="run.eval",
        critics=[],
        combiner=CombinerRecord(),
    ).model_dump_json()


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"ok": True}


def test_ideas_are_listed_without_the_answer_keys(client: TestClient) -> None:
    assert client.get("/api/ideas").json() == [
        {
            "id": "shelfsense",
            "title": "ShelfSense: a fridge camera",
            "chars": len(SAMPLE),
            "sample": True,
        },
        {
            "id": "untitled",
            "title": "untitled",
            "chars": len("An idea without a heading."),
            "sample": True,
        },
    ]


def test_idea_is_served_with_its_text(client: TestClient) -> None:
    assert client.get("/api/ideas/shelfsense").json() == {
        "id": "shelfsense",
        "title": "ShelfSense: a fridge camera",
        "text": SAMPLE,
    }
    assert client.get("/api/ideas/missing").status_code == 404


@pytest.mark.parametrize(
    "idea_id", ["shelfsense.planted-flaws", "pumpwatch.expected", "ShelfSense", "..%2Fsecret"]
)
def test_idea_ids_that_are_not_plain_slugs_are_refused(
    client: TestClient, root: Path, idea_id: str
) -> None:
    (root / "secret.md").write_text("# Secret\n\nNot an idea.", encoding="utf-8")

    response = client.get(f"/api/ideas/{idea_id}")

    assert response.status_code in (404, 422)
    assert "Secret" not in response.text


def test_run_on_a_sample_idea_starts_the_panel_process(
    client: TestClient, root: Path, launcher: FakeLauncher
) -> None:
    run_id = start(client)

    assert re.fullmatch(r"[0-9]{8}-[0-9]{6}-shelfsense", run_id)
    folder = Path("runs") / run_id
    [(command, cwd, output)] = launcher.calls
    assert command == [
        sys.executable,
        "-m",
        "skeptic.cli",
        "run",
        str(folder / "idea.md"),
        "--out",
        str(folder / "record.json"),
        "--progress",
        str(folder / "progress.jsonl"),
        "--idea-id",
        "shelfsense",
        "--log-dir",
        "logs",
    ]
    assert cwd == root.resolve()
    assert output == root.resolve() / folder / "process.log"
    assert (root / folder / "idea.md").read_text(encoding="utf-8") == SAMPLE


def test_run_on_pasted_text_puts_the_title_above_the_text(
    client: TestClient, root: Path, launcher: FakeLauncher
) -> None:
    run_id = start(client, {"title": "  Bike repair at work ", "text": PASTED})

    assert run_id.endswith("-bike-repair-at-work")
    [(command, _, _)] = launcher.calls
    assert command[command.index("--idea-id") + 1] == "bike-repair-at-work"
    idea = (root / "runs" / run_id / "idea.md").read_text(encoding="utf-8")
    assert idea == f"# Bike repair at work\n\n{PASTED.strip()}\n"
    assert client.get(f"/api/runs/{run_id}").json()["idea_title"] == "Bike repair at work"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"idea_id": "shelfsense", "title": "Title", "text": PASTED},
        {"idea_id": "../ideas/shelfsense"},
        {"idea_id": "shelfsense.planted-flaws"},
        {"idea_id": "shelfsense", "extra": 1},
        {"text": PASTED},
        {"title": "Title"},
        {"title": "Title", "text": "Too short."},
        {"title": "Title", "text": " " * 300},
        {"title": "Title", "text": "x" * 20_001},
        {"title": "Two\nlines", "text": PASTED},
        {"title": " ", "text": PASTED},
    ],
)
def test_invalid_run_requests_are_refused(
    client: TestClient, launcher: FakeLauncher, body: dict[str, Any]
) -> None:
    assert client.post("/api/runs", json=body).status_code == 422
    assert launcher.calls == []


def test_pasted_text_may_have_20000_characters(client: TestClient) -> None:
    start(client, {"title": "Long", "text": "x" * 20_000})


def test_run_on_a_missing_sample_idea_is_not_found(
    client: TestClient, launcher: FakeLauncher
) -> None:
    assert client.post("/api/runs", json={"idea_id": "missing"}).status_code == 404
    assert launcher.calls == []


def test_second_run_waits_until_the_first_has_ended(
    client: TestClient, launcher: FakeLauncher
) -> None:
    first = start(client)

    response = client.post("/api/runs", json={"idea_id": "untitled"})
    assert response.status_code == 409
    assert first in response.json()["detail"]
    assert len(launcher.calls) == 1

    launcher.processes[0].returncode = 0
    start(client, {"idea_id": "untitled"})


def test_run_status_follows_the_process_then_its_record(
    client: TestClient, root: Path, launcher: FakeLauncher
) -> None:
    run_id = start(client)
    folder = root / "runs" / run_id

    [summary] = client.get("/api/runs").json()
    assert summary["id"] == run_id
    assert summary["idea_title"] == "ShelfSense: a fridge camera"
    assert summary["status"] == "running"
    assert summary["finished_at"] is None
    assert summary["started_at"].startswith(
        f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}T{run_id[9:11]}:{run_id[11:13]}"
    )

    progress = ProgressWriter(folder / "progress.jsonl")
    progress.write(RunStarted(critics=list(CRITICS), models={"market": "ollama/qwen"}))
    progress.write(CriticStarted(critic="market", model="ollama/qwen"))
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "running"
    assert [e["event"] for e in detail["events"]] == ["run_started", "critic_started"]
    assert detail["events"][1]["critic"] == "market"
    assert detail["idea_text"] == SAMPLE
    assert (detail["record"], detail["error"]) == (None, None)

    progress.write(CriticFinished(critic="market", ok=True, issues=3, overall="weak", error=None))
    (folder / "record.json").write_text(record_json(), encoding="utf-8")
    launcher.processes[0].returncode = 0
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "success"
    assert detail["finished_at"] == "2026-11-21T23:20:00Z"
    assert detail["record"]["status"] == "success"
    assert detail["record"]["idea"] == SAMPLE
    assert detail["events"][-1] == {
        "time": detail["events"][-1]["time"],
        "event": "critic_finished",
        "critic": "market",
        "ok": True,
        "issues": 3,
        "overall": "weak",
        "error": None,
    }
    assert detail["error"] is None


def test_failed_run_takes_its_error_from_the_record(
    client: TestClient, root: Path, launcher: FakeLauncher
) -> None:
    run_id = start(client)
    (root / "runs" / run_id / "record.json").write_text(
        record_json("error", "no critic returned a usable verdict"), encoding="utf-8"
    )
    launcher.processes[0].returncode = 1

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "error"
    assert detail["error"] == "no critic returned a usable verdict"
    assert detail["record"]["status"] == "error"


def test_process_that_ends_without_a_record_says_why(
    client: TestClient, root: Path, launcher: FakeLauncher
) -> None:
    run_id = start(client)
    (root / "runs" / run_id / "process.log").write_text(
        "Traceback (most recent call last):\n  File 'cli.py'\nRuntimeError: boom\n",
        encoding="utf-8",
    )
    launcher.processes[0].returncode = 1

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "error"
    assert "exit code 1" in detail["error"]
    assert detail["error"].endswith("RuntimeError: boom")
    assert detail["finished_at"] is not None


def test_progress_line_cut_off_inside_a_character_is_left_out(
    client: TestClient, root: Path
) -> None:
    run_id = start(client)
    progress = root / "runs" / run_id / "progress.jsonl"
    ProgressWriter(progress).write(CriticStarted(critic="market", model="ollama/qwen"))
    with progress.open("ab") as file:
        file.write(b'{"time": "2026-11-21T23:15:02Z", "event": "critic_finished", "error": "\xc3')

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert [e["event"] for e in response.json()["events"]] == ["critic_started"]


def test_after_a_restart_a_run_without_process_or_record_was_interrupted(
    root: Path, launcher: FakeLauncher
) -> None:
    with TestClient(create_app(root, launcher, HOSTS)) as before:
        unfinished = start(before)
        launcher.processes[0].returncode = 0
        finished = start(before)
        (root / "runs" / finished / "record.json").write_text(record_json(), encoding="utf-8")

    with TestClient(create_app(root, FakeLauncher(), HOSTS)) as after:
        statuses = {run["id"]: run["status"] for run in after.get("/api/runs").json()}
        assert statuses == {unfinished: "error", finished: "success"}
        assert after.get(f"/api/runs/{unfinished}").json()["error"] == "interrupted"


def test_stop_terminates_the_process(client: TestClient, launcher: FakeLauncher) -> None:
    run_id = start(client)

    response = client.post(f"/api/runs/{run_id}/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert response.json()["finished_at"] is not None
    assert launcher.processes[0].terminated
    assert not launcher.processes[0].killed
    detail = client.get(f"/api/runs/{run_id}").json()
    assert (detail["status"], detail["error"]) == ("stopped", None)
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 409
    start(client)


def test_stop_kills_a_process_that_ignores_terminate(
    client: TestClient, launcher: FakeLauncher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("skeptic.server.STOP_TIMEOUT_SECONDS", 0.0)
    launcher.ignores_terminate = True
    run_id = start(client)

    assert client.post(f"/api/runs/{run_id}/stop").json()["status"] == "stopped"
    assert launcher.processes[0].killed


def test_stop_of_a_run_that_is_not_there_is_not_found(client: TestClient) -> None:
    assert client.post("/api/runs/20261121-100000-missing/stop").status_code == 404


def test_runs_are_listed_newest_first(client: TestClient, root: Path) -> None:
    for run_id in ["20261121-100000-older", "20261121-110000-newer"]:
        (root / "runs" / run_id).mkdir(parents=True)
        (root / "runs" / run_id / "idea.md").write_text(SAMPLE, encoding="utf-8")
    (root / "runs" / "notes").mkdir()

    runs = client.get("/api/runs").json()

    assert [run["id"] for run in runs] == ["20261121-110000-newer", "20261121-100000-older"]
    assert runs[0]["started_at"] == "2026-11-21T11:00:00Z"


def test_run_ids_must_have_the_run_id_format(client: TestClient) -> None:
    assert client.get("/api/runs/..%2Fideas").status_code in (404, 422)
    assert client.get("/api/runs/not-a-run").status_code == 422
    assert client.get("/api/runs/20261121-100000-missing").status_code == 404


def test_shutdown_terminates_a_run_still_going(root: Path, launcher: FakeLauncher) -> None:
    with TestClient(create_app(root, launcher, HOSTS)) as client:
        start(client)

    assert launcher.processes[0].terminated


def test_built_web_ui_is_served_with_a_fallback_to_its_index(
    client: TestClient, root: Path
) -> None:
    assert client.get("/").status_code == 404

    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SkeptiC</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    assert "<title>SkeptiC</title>" in client.get("/").text
    assert "<title>SkeptiC</title>" in client.get("/runs/20261121-100000-a").text
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/missing").status_code == 404
    assert client.get("/api/missing").json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "base_url", ["http://127.0.0.1:8000", "http://localhost:5173", "http://[::1]:8000"]
)
def test_requests_to_a_local_host_name_are_served(
    root: Path, launcher: FakeLauncher, base_url: str
) -> None:
    with TestClient(create_app(root, launcher), base_url=base_url) as client:
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8000", "localhost.evil.example", "testserver"]
)
def test_requests_to_another_host_are_refused(
    root: Path, launcher: FakeLauncher, host: str
) -> None:
    # After DNS rebinding, a hostile page reaches the server under its own host name.
    with TestClient(create_app(root, launcher), base_url=f"http://{host}") as client:
        assert client.get("/api/runs").status_code == 400
        assert client.post("/api/runs", json={"idea_id": "shelfsense"}).status_code == 400
    assert launcher.calls == []


def test_serve_answers_the_address_it_listens_on() -> None:
    assert serve_hosts("127.0.0.1") == list(LOCAL_HOSTS)
    assert serve_hosts("localhost") == list(LOCAL_HOSTS)
    assert serve_hosts("::1") == list(LOCAL_HOSTS)
    assert serve_hosts("192.168.1.20") == [*LOCAL_HOSTS, "192.168.1.20"]
    assert serve_hosts("fd00::5") == [*LOCAL_HOSTS, "[fd00::5]"]
    # A wildcard is not a name; other machines' names come in as extras.
    assert serve_hosts("0.0.0.0") == list(LOCAL_HOSTS)
    assert serve_hosts("::") == list(LOCAL_HOSTS)
    assert serve_hosts("0.0.0.0", ["laptop.local", "192.168.1.20"]) == [
        *LOCAL_HOSTS,
        "laptop.local",
        "192.168.1.20",
    ]


@pytest.mark.parametrize(
    "origin", ["http://evil.example", "http://localhost:5173", "http://testserver:8000", "null"]
)
def test_run_from_another_origin_is_refused(
    client: TestClient, launcher: FakeLauncher, origin: str
) -> None:
    response = client.post("/api/runs", json={"idea_id": "shelfsense"}, headers={"origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": "cross-origin request refused"}
    assert launcher.calls == []


def test_stop_from_another_origin_is_refused(client: TestClient, launcher: FakeLauncher) -> None:
    run_id = start(client)

    # A no-cors POST without a body, which a browser sends without asking the server first.
    response = client.post(f"/api/runs/{run_id}/stop", headers={"origin": "http://evil.example"})

    assert response.status_code == 403
    assert not launcher.processes[0].terminated
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        # The Vite dev server forwards /api with the browser's Host and Origin.
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
        # curl and the CLI send no Origin.
        ("http://127.0.0.1:8000", None),
    ],
)
def test_runs_from_the_same_origin_or_without_one_start_and_stop(
    root: Path, launcher: FakeLauncher, base_url: str, origin: str | None
) -> None:
    headers = {"origin": origin} if origin else {}
    with TestClient(create_app(root, launcher), base_url=base_url) as client:
        response = client.post("/api/runs", json={"idea_id": "shelfsense"}, headers=headers)
        assert response.status_code == 202
        stop = client.post(f"/api/runs/{response.json()['id']}/stop", headers=headers)
        assert stop.status_code == 200
    assert launcher.processes[0].terminated


def test_reads_from_another_origin_are_answered_without_cors(client: TestClient) -> None:
    # Reads are guarded by the host check, and without a CORS header the browser
    # keeps the answer from the other page.
    response = client.get("/api/runs", headers={"origin": "http://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_titles_and_slugs() -> None:
    assert markdown_title("Intro\n\n## Pump watch ##\n# Later", "x") == "Pump watch"
    assert markdown_title("# Using C#", "x") == "Using C#"
    assert markdown_title("#hashtag only", "fallback") == "fallback"
    assert slugify("Äänipankki: a sound bank!") == "aanipankki-a-sound-bank"
    assert slugify("???") == "idea"
    assert len(slugify("word " * 30)) <= 40


def test_start_process_sends_what_the_process_prints_to_a_file(tmp_path: Path) -> None:
    output = tmp_path / "process.log"
    command = [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]

    process = start_process(command, tmp_path, output)

    assert process.wait(timeout=60) == 0
    assert output.read_text(encoding="utf-8").splitlines() == ["out", "err"]

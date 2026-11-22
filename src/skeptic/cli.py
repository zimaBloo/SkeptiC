"""The skeptic command.

    skeptic run IDEA [--lineup NAME] [--reasoning LEVEL] [--max-tokens N]
                [--role ROLE=MODEL ...] [--out FILE] [--progress FILE] [--idea-id ID]
        Run the panel on an idea file and write the run's record as JSON.
    skeptic export LOG [--out FILE]
        Write the record of an earlier run from its Inspect log.
    skeptic schema
        Print the JSON Schema of the record.
    skeptic serve [--host HOST] [--port PORT] [--allowed-host NAME ...]
        Start the web API (see skeptic.server).

Without --out, the record is written next to the Inspect log, with .json in
place of .eval. API keys are read from the environment or from a .env file in
the current folder or above it.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from inspect_ai import eval as inspect_eval
from inspect_ai.log import EvalLog

from skeptic.lineups import (
    LINEUP_NAMES,
    REASONING_LEVELS,
    check_reasoning,
    get_lineup,
    load_env,
    missing_key,
)
from skeptic.panel import skeptic_panel
from skeptic.record import PanelRecord, record_from_log, record_json_schema
from skeptic.server import create_app, serve_hosts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skeptic", description="A panel of AI critics.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the panel on an idea file")
    run.add_argument("idea", type=Path, help="text or Markdown file with the idea")
    run.add_argument("--out", type=Path, help="where to write the record")
    run.add_argument(
        "--progress", metavar="FILE", help="file to append progress events to, as JSON lines"
    )
    run.add_argument(
        "--idea-id", metavar="ID", help="the idea's id in the record (default: the file name)"
    )
    run.add_argument(
        "--lineup",
        choices=LINEUP_NAMES,
        default="local",
        help="the models to run on: local ones on this computer (the default), or frontier "
        "ones hosted by four labs and reached through OpenRouter",
    )
    run.add_argument(
        "--reasoning",
        choices=REASONING_LEVELS,
        help="how much the models reason (default: the lineup's level)",
    )
    run.add_argument(
        "--max-tokens",
        type=int,
        metavar="N",
        help="most tokens one reply may use, reasoning included (default: the lineup's limit)",
    )
    run.add_argument(
        "--role",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="run a critic or the combiner on another model, e.g. risk=ollama/qwen3.5:9b",
    )
    run.add_argument("--log-dir", default="logs", help="where Inspect writes its log")

    export = commands.add_parser("export", help="write the record of an earlier run")
    export.add_argument("log", type=Path, help="Inspect log of a panel run (.eval)")
    export.add_argument("--out", type=Path, help="where to write the record")

    commands.add_parser("schema", help="print the JSON Schema of the record")

    serve = commands.add_parser("serve", help="start the web API")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on")
    serve.add_argument("--port", type=int, default=8000, help="port to listen on")
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="NAME",
        help="another host name or address the API should answer to (repeatable)",
    )

    args = parser.parse_args(argv)
    if args.command == "serve":
        hosts = serve_hosts(args.host, args.allowed_host)
        print(f"Answering requests addressed to: {', '.join(hosts)}", file=sys.stderr)
        app = create_app(allowed_hosts=hosts)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
    if args.command == "schema":
        print(json.dumps(record_json_schema(), indent=2))
        return 0
    if args.command == "export":
        try:
            record = record_from_log(args.log)
        except ValueError as error:
            print(f"skeptic export: {error}", file=sys.stderr)
            return 2
        _write(record, args.out or args.log.with_suffix(".json"))
        return 0 if record.status == "success" else 1

    if not args.idea.is_file():
        parser.error(f"no such idea file: {args.idea}")
    if args.max_tokens is not None and args.max_tokens < 1:
        parser.error("--max-tokens must be a positive number")
    try:
        check_reasoning(get_lineup(args.lineup), args.reasoning or "default")
    except ValueError as error:
        parser.error(str(error))
    roles = dict(_parse_role(parser, value) for value in args.role)
    # The same .env Inspect itself would read; the models are set up before it reads it.
    load_env(Path.cwd())
    key = missing_key(get_lineup(args.lineup))
    if key is not None:
        print(
            f"skeptic run: the {args.lineup} lineup needs an API key: put {key}=... in .env",
            file=sys.stderr,
        )
        return 2
    [log] = inspect_eval(
        skeptic_panel(
            idea=str(args.idea),
            idea_id=args.idea_id,
            lineup=args.lineup,
            reasoning=args.reasoning,
            max_tokens=args.max_tokens,
            progress_file=args.progress,
        ),
        model_roles=roles or None,
        log_dir=args.log_dir,
        display="plain",
    )
    record = record_from_log(log)
    _write(record, args.out or _default_out(log))
    if record.status != "success":
        print(f"the run failed: {record.error}", file=sys.stderr)
        return 1
    return 0


def _parse_role(parser: argparse.ArgumentParser, value: str) -> tuple[str, str]:
    role, _, model = value.partition("=")
    if not role or not model:
        parser.error(f"--role takes ROLE=MODEL, got {value!r}")
    return role, model


def _default_out(log: EvalLog) -> Path:
    return Path(str(log.location)).with_suffix(".json")


def _write(record: PanelRecord, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    print(f"record: {path}")


if __name__ == "__main__":
    raise SystemExit(main())

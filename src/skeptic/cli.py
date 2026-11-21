"""The skeptic command.

    skeptic run IDEA [--out FILE] [--role ROLE=MODEL ...]
        Run the panel on an idea file and write the run's record as JSON.
    skeptic export LOG [--out FILE]
        Write the record of an earlier run from its Inspect log.
    skeptic schema
        Print the JSON Schema of the record.

Without --out, the record is written next to the Inspect log, with .json in
place of .eval.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.log import EvalLog

from skeptic.panel import skeptic_panel
from skeptic.record import PanelRecord, record_from_log, record_json_schema


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skeptic", description="A panel of AI critics.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the panel on an idea file")
    run.add_argument("idea", type=Path, help="text or Markdown file with the idea")
    run.add_argument("--out", type=Path, help="where to write the record")
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

    args = parser.parse_args(argv)
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
    roles = dict(_parse_role(parser, value) for value in args.role)
    [log] = inspect_eval(
        skeptic_panel(idea=str(args.idea)),
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

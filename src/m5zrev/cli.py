"""Commands for the EURGBP M5 book. They do not send a live order."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from m5zrev.data import DataUnavailableError, fetch_mt5, load_bars, load_events, load_spec, probe_mt5
from m5zrev.histdata import prepare_histdata
from m5zrev.params import Params, load_params
from m5zrev.report import write_unavailable
from m5zrev.session import require_utc

M5 = timedelta(minutes=5)
H1 = timedelta(hours=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="m5z")
    parser.add_argument("--config", default="configs/m5zrev.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--output", default="artifacts/m5zrev-probe")

    prepare = sub.add_parser("prepare-histdata")
    prepare.add_argument("--raw", default="data/m15/raw")
    prepare.add_argument("--out", default="data/m5z")

    export = sub.add_parser("export-events")
    export.add_argument("--csv", required=True)
    export.add_argument("--out", required=True)

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--out-dir", default="data/m5z")
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True)
    fetch.add_argument("--server-utc-offset-hours", type=float, required=True)

    study = sub.add_parser("study")
    _add_data_args(study)
    study.add_argument("--output", default="artifacts/m5zrev")
    study.add_argument("--study-spread-pips", type=float, default=1.0)

    args = parser.parse_args(argv)
    try:
        params = load_params(Path(args.config))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        if args.command == "status":
            return _status(Path(args.output), params)
        if args.command == "prepare-histdata":
            m5_path, h1_path = prepare_histdata(Path(args.raw), Path(args.out))
            print(m5_path)
            print(h1_path)
            return 0
        if args.command == "export-events":
            return _export_events(Path(args.csv), Path(args.out))
        if args.command == "fetch":
            return _fetch(args)
        if args.command == "study":
            return _study(args, params)
    except DataUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"unknown command {args.command}", file=sys.stderr)
    return 2


def environment_notes() -> str:
    """What this machine can and cannot see. No broker offset is inferred."""
    probe = probe_mt5()
    return f"{probe.detail}\n\n{compiler_note()}"


def compiler_note() -> str:
    found = shutil.which("metaeditor64") or shutil.which("metaeditor")
    if found:
        return f"PATH 上有 {found}。这次没有调用它编译 EA。"
    applications = sorted(Path("/Applications").glob("*Meta*")) if Path("/Applications").is_dir() else []
    if applications:
        names = ", ".join(path.name for path in applications)
        return f"/Applications 里有 {names}。这次没有完成 MetaEditor 编译。"
    return "PATH 和 /Applications 里没有找到 MetaEditor。EA 源码没有编译。"


def _status(output: Path, params: Params) -> int:
    detail = environment_notes()
    write_unavailable(output, detail, params)
    print(detail)
    print(output / "verdict.md")
    return 0


def _export_events(source: Path, dest: Path) -> int:
    events = load_events(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(f"{int(item.timestamp())}\n" for item in events), encoding="utf-8")
    print(f"{len(events)} {dest}")
    return 0


def _fetch(args: argparse.Namespace) -> int:
    probe = fetch_mt5(
        Path(args.out_dir),
        start=_parse(args.start),
        end=_parse(args.end),
        server_utc_offset_hours=args.server_utc_offset_hours,
    )
    print(probe.detail)
    return 0


def _study(args: argparse.Namespace, params: Params) -> int:
    from m5zrev.study import run_study

    m5 = load_bars(Path(args.m5), duration=M5, timestamp_is_open=args.timestamp_is_open)
    h1 = load_bars(Path(args.h1), duration=H1, timestamp_is_open=args.timestamp_is_open) if args.h1 else None
    spec = load_spec(Path(args.spec))
    events = load_events(Path(args.events)) if args.events else ()
    label = run_study(
        m5,
        h1,
        spec,
        events,
        params,
        Path(args.output),
        study_spread_pips=args.study_spread_pips,
        notes=environment_notes(),
    )
    print(label)
    print(Path(args.output) / "verdict.md")
    return 0


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--m5", required=True)
    parser.add_argument("--h1")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--events")
    parser.add_argument("--timestamp-is-open", action="store_true")


def _parse(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise DataUnavailableError(f"{value} is naive; pass a timezone")
    return require_utc(parsed)


if __name__ == "__main__":
    raise SystemExit(main())

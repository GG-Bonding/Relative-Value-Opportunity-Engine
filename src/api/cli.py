"""rv commands. EURGBP only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from api.service import EngineService
from data.laboratory import build_laboratory
from data.store import PitStore
from domain.config import EngineConfig, load_config
from domain.errors import UnsupportedPairError
from domain.models import require_eurgbp
from evaluation.report import write_v1_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rv")
    argv = _flags_before_command(list(sys.argv[1:] if argv is None else argv))
    parser.add_argument("--db", default="data/normalized/eurgbp.duckdb")
    parser.add_argument("--config", default="configs/eurgbp.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--laboratory", action="store_true")
    ingest.add_argument("--jin10", action="store_true")

    sub.add_parser("validate-data")
    for name in (
        "snapshot",
        "factors",
        "mechanisms",
        "expectations",
        "fair-value",
        "regime",
        "alpha-health",
        "opportunity",
        "signal",
        "explain",
        "backtest",
    ):
        command = sub.add_parser(name)
        command.add_argument("pair")
    sub.add_parser("opportunities")
    sub.add_parser("replay")
    sub.add_parser("experiments")
    sub.add_parser("compare-models")
    report_command = sub.add_parser("research-report")
    report_command.add_argument("--output", default="artifacts/v1-research-report.md")
    forward = sub.add_parser("forward")
    forward.add_argument("--mode", choices=["shadow", "paper", "live"], required=True)
    forward.add_argument("--tick", required=True)
    forward.add_argument("--rates", required=True)
    forward.add_argument("--events")
    forward.add_argument("--fair-value", dest="fair_value", type=float)

    args = parser.parse_args(argv)
    if args.command == "mechanisms":
        return _mechanisms(args.pair)
    config = load_config(Path(args.config))
    if args.command == "ingest":
        if args.laboratory and args.jin10:
            print("choose one ingest source: --laboratory or --jin10", file=sys.stderr)
            return 2
        if not args.laboratory and not args.jin10:
            print("V1 ingest accepts --laboratory or --jin10.", file=sys.stderr)
            return 2
        path = Path(args.db)
        path.parent.mkdir(parents=True, exist_ok=True)
        store = PitStore(path)
        try:
            if args.laboratory:
                build_laboratory(store, config)
                print(json.dumps({"db": str(path), "rows": store.count("market_observations")}))
            else:
                _load_local_env()
                from data.jin10_client import Jin10Client, Jin10Error, mcp_url_from_env, token_from_env
                from data.jin10_ingest import ingest_jin10

                try:
                    client = Jin10Client(token_from_env(), mcp_url_from_env())
                except Jin10Error as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
                try:
                    report = ingest_jin10(store, client)
                except Jin10Error as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
                finally:
                    client.close()
                report["db"] = str(path)
                print(json.dumps(report, ensure_ascii=False))
        finally:
            store.close()
        return 0
    if args.command == "research-report":
        write_v1_report(Path(args.db), Path(args.output), config)
        print(args.output)
        return 0
    if args.command == "forward":
        return _forward(args, config)
    if not Path(args.db).exists():
        print(f"no store at {args.db}. Run rv ingest --laboratory first.", file=sys.stderr)
        return 2
    store = PitStore(args.db)
    service = EngineService(store, config)
    try:
        return _dispatch(service, args)
    finally:
        store.close()


def _forward(args: argparse.Namespace, config: EngineConfig) -> int:
    from datetime import datetime

    from domain.errors import DataValidationError, PointInTimeError
    from domain.timeutil import UTC
    from forward.cycle import ForwardBook, run_forward
    from forward.events import load_events
    from forward.mt5 import load_mt5_tick
    from forward.rates import load_rates

    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tick = load_mt5_tick(args.tick)
        rates = load_rates(args.rates)
        events = load_events(args.events) if args.events else []
        store = PitStore(path)
        try:
            result = run_forward(
                store,
                config,
                args.mode,
                ForwardBook(tick=tick, rates=rates, events=events),
                ingested_at=datetime.now(tz=UTC),
                fair_value=args.fair_value,
            )
        finally:
            store.close()
    except (DataValidationError, PointInTimeError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    if args.mode == "live":
        return 2
    return 0


def _load_local_env() -> None:
    """Fill missing Jin10 settings from a local .env. Existing variables win."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flags_before_command(argv: list[str]) -> list[str]:
    """`--db` and `--config` are accepted before or after the subcommand."""
    flags = {"--db", "--config"}
    pulled: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in flags and index + 1 < len(argv):
            pulled.extend([token, argv[index + 1]])
            index += 2
        else:
            rest.append(token)
            index += 1
    return pulled + rest


def _mechanisms(pair: str) -> int:
    try:
        require_eurgbp(pair)
    except UnsupportedPairError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    from mechanisms.registry import registry

    print(json.dumps([item.model_dump(mode="json") for item in registry()], indent=2))
    return 0


def _dispatch(service: EngineService, args: argparse.Namespace) -> int:
    pair = getattr(args, "pair", "EURGBP")
    try:
        if pair:
            require_eurgbp(pair)
    except UnsupportedPairError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == "validate-data":
        print(json.dumps(service.validate(), indent=2))
    elif args.command == "factors" or args.command == "snapshot":
        print(json.dumps(service.factors(), indent=2, default=str))
    elif args.command == "expectations":
        print(json.dumps(service.expectations(), indent=2, default=str))
    elif args.command == "fair-value":
        print(json.dumps(service.fair_value(), indent=2, default=str))
    elif args.command == "regime":
        print(json.dumps(service.regime(), indent=2))
    elif args.command == "alpha-health":
        print(json.dumps(service.mechanism_state(), indent=2, default=str))
    elif args.command == "signal":
        print(json.dumps(service.fair_value(), indent=2, default=str))
    elif args.command in {"opportunity", "opportunities", "explain"}:
        opportunity = service.current_opportunity()
        print(json.dumps(opportunity.model_dump(mode="json"), indent=2, default=str))
    elif args.command == "backtest":
        from datetime import datetime

        from backtest.engine import run_backtest
        from domain.timeutil import UTC

        result = run_backtest(
            service.store,
            service.config,
            datetime(2014, 1, 1, tzinfo=UTC),
            service.latest_as_of(),
        )
        print(json.dumps(result.run.model_dump(mode="json"), indent=2, default=str))
    elif args.command == "replay":
        print(json.dumps({"note": "Re-run rv backtest EURGBP. The run hash must match the saved hash."}))
    elif args.command == "experiments":
        print(json.dumps({"registry": "artifacts/experiments.jsonl"}))
    elif args.command == "compare-models":
        print(json.dumps({"champion": "eurgbp-rolling-ridge-v1", "note": "See the V1 research report."}))
    else:
        print(f"unknown command {args.command}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

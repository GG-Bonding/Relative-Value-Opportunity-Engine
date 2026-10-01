"""rv commands. EURGBP only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from api.service import EngineService
from data.laboratory import build_laboratory
from data.store import PitStore
from domain.config import load_config
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
    report = sub.add_parser("research-report")
    report.add_argument("--output", default="artifacts/v1-research-report.md")

    args = parser.parse_args(argv)
    if args.command == "mechanisms":
        return _mechanisms(args.pair)
    config = load_config(Path(args.config))
    if args.command == "ingest":
        if not args.laboratory:
            print("V1 ingest accepts --laboratory. Vendor PIT history is not bundled.", file=sys.stderr)
            return 2
        path = Path(args.db)
        path.parent.mkdir(parents=True, exist_ok=True)
        store = PitStore(path)
        build_laboratory(store, config)
        print(json.dumps({"db": str(path), "rows": store.count("market_observations")}))
        store.close()
        return 0
    if args.command == "research-report":
        write_v1_report(Path(args.db), Path(args.output), config)
        print(args.output)
        return 0
    if not Path(args.db).exists():
        print(f"no store at {args.db}. Run rv ingest --laboratory first.", file=sys.stderr)
        return 2
    store = PitStore(args.db)
    service = EngineService(store, config)
    try:
        return _dispatch(service, args)
    finally:
        store.close()


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

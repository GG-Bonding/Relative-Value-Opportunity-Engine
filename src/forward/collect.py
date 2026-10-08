"""Pull the three layers for one round. They are not substitutes for each other."""

from __future__ import annotations

from datetime import date, datetime

import httpx

from data.jin10_client import Jin10Client, Jin10Error, mcp_url_from_env, token_from_env
from domain.errors import DataValidationError
from domain.timeutil import ensure_utc
from forward.mt5 import Mt5Tick
from forward.official_rates import align_same_day, download_de2y, download_uk2y
from forward.prints import InformationPrint, read_information
from forward.round import RoundInputs
from forward.terminal import read_eurgbp_tick


def load_round_inputs(
    terminal: str | None,
    as_of: datetime,
    curves: tuple[dict[date, float], dict[date, float], str | None] | None = None,
) -> RoundInputs:
    prints, information_error = _information()
    uk, de, rate_error = _curves() if curves is None else curves
    tick, quote_error = _quote(terminal)
    limit = ensure_utc(as_of)
    if tick is not None and tick.time < limit:
        limit = tick.time
    return RoundInputs(
        prints=prints,
        information_error=information_error,
        rates=align_same_day(uk, de, limit),
        rate_error=rate_error,
        tick=tick,
        quote_error=quote_error,
    )


def _information() -> tuple[list[InformationPrint], str | None]:
    try:
        client = Jin10Client(token_from_env(), mcp_url_from_env())
    except Jin10Error as exc:
        return [], str(exc)
    try:
        return read_information(client)
    except Jin10Error as exc:
        return [], str(exc)
    finally:
        client.close()


def fetch_curves() -> tuple[dict[date, float], dict[date, float], str | None]:
    return _curves()


def _curves() -> tuple[dict[date, float], dict[date, float], str | None]:
    uk: dict[date, float] = {}
    de: dict[date, float] = {}
    errors: list[str] = []
    try:
        with httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": "relative-value-engine"},
        ) as client:
            uk, de, errors = _download(client)
    except httpx.HTTPError as exc:
        errors.append(f"rates: {exc}")
    if not uk and not de and not errors:
        errors.append("rates: official curves were empty")
    return uk, de, "; ".join(errors) or None


def _download(client: httpx.Client) -> tuple[dict[date, float], dict[date, float], list[str]]:
    uk: dict[date, float] = {}
    de: dict[date, float] = {}
    errors: list[str] = []
    try:
        uk = download_uk2y(client)
    except (DataValidationError, httpx.HTTPError, OSError, ValueError) as exc:
        errors.append(f"UK2Y: {exc}")
    try:
        de = download_de2y(client)
    except (DataValidationError, httpx.HTTPError, OSError, ValueError) as exc:
        errors.append(f"DE2Y: {exc}")
    return uk, de, errors


def _quote(terminal: str | None) -> tuple[Mt5Tick | None, str | None]:
    try:
        return read_eurgbp_tick(terminal), None
    except DataValidationError as exc:
        return None, str(exc)

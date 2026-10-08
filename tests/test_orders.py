"""A signal can flip the EURGBP book. A watch cannot."""

from __future__ import annotations

import sys
from pathlib import Path

from forward.orders import Held, align_eurgbp, plan_orders


def test_watch_and_the_same_side_do_not_add_a_ticket() -> None:
    assert plan_orders("WATCH", "UPDATE", [Held(1, "LONG", 0.01)], 0.01).open_side is None
    assert plan_orders("WATCH", "UPDATE", [Held(1, "LONG", 0.01)], 0.01).close == ()
    same = plan_orders("SHORT", "SIGNAL", [Held(7, "SHORT", 0.01)], 0.01)
    assert same.close == ()
    assert same.open_side is None


def test_a_short_closes_the_long_and_a_reversal_only_flattens() -> None:
    flipped = plan_orders("SHORT", "SIGNAL", [Held(3, "LONG", 0.02)], 0.01)
    assert [item.ticket for item in flipped.close] == [3]
    assert flipped.open_side == "SHORT"
    flat = plan_orders("SHORT", "REVERSAL", [Held(3, "LONG", 0.02), Held(4, "SHORT", 0.01)], 0.01)
    assert [item.ticket for item in flat.close] == [3, 4]
    assert flat.open_side is None


def test_align_sells_the_long_then_opens_the_short(tmp_path: Path, monkeypatch) -> None:
    terminal = _broker([_position(11, 0, 0.02)])
    monkeypatch.setitem(sys.modules, "MetaTrader5", terminal)
    report = align_eurgbp("C:/terminal64.exe", "SHORT", "SIGNAL", 0.01, tmp_path / "orders.jsonl")
    assert report["order_sent"] is True
    assert [item["type"] for item in terminal.requests] == [1, 1]
    assert terminal.requests[0]["position"] == 11
    assert "position" not in terminal.requests[1]
    assert terminal.requests[1]["symbol"] == "EURGBP"
    assert terminal.requests[1]["volume"] == 0.01
    assert terminal.requests[1]["magic"] == 20261007


def test_align_ignores_another_programs_position_and_a_watch(tmp_path: Path, monkeypatch) -> None:
    terminal = _broker([_position(11, 0, 0.02, magic=1)])
    monkeypatch.setitem(sys.modules, "MetaTrader5", terminal)
    held = align_eurgbp("C:/terminal64.exe", "SHORT", "SIGNAL", 0.01, tmp_path / "orders.jsonl")
    assert held["order_sent"] is True
    assert len(terminal.requests) == 1
    assert "position" not in terminal.requests[0]
    terminal.requests.clear()
    quiet = align_eurgbp("C:/terminal64.exe", "WATCH", "UPDATE", 0.01, tmp_path / "orders.jsonl")
    assert quiet["order_sent"] is False
    assert terminal.requests == []


class _Result:
    retcode = 10009
    comment = "done"


class _Info:
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 50
    filling_mode = 2


class _Tick:
    bid = 0.84576
    ask = 0.84578


def _position(ticket: int, side: int, volume: float, magic: int = 20261007) -> object:
    return type("Position", (), {"ticket": ticket, "type": side, "volume": volume, "magic": magic})()


def _broker(positions: list[object]) -> object:
    class _Terminal:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        POSITION_TYPE_BUY = 0
        TRADE_ACTION_DEAL = 1
        ORDER_TIME_GTC = 0
        ORDER_FILLING_IOC = 1
        requests: list[dict[str, object]] = []

        def initialize(self, path: str | None = None) -> bool:
            return True

        def shutdown(self) -> None:
            return None

        def symbol_select(self, symbol: str, enable: bool) -> bool:
            return symbol == "EURGBP"

        def symbol_info(self, symbol: str) -> _Info:
            return _Info()

        def symbol_info_tick(self, symbol: str) -> _Tick:
            return _Tick()

        def positions_get(self, symbol: str) -> list[object]:
            return positions

        def order_send(self, request: dict[str, object]) -> _Result:
            self.requests.append(request)
            return _Result()

        def last_error(self) -> tuple[int, str]:
            return 1, "ok"

    return _Terminal()

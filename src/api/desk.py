"""A local page for the EURGBP forward log. It reads the append-only record and sends nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from forward.watch import assessment

PIP = 0.0001


def create_desk_app(log_path: str | Path) -> FastAPI:
    app = FastAPI(title="EURGBP desk", version="0.1.0")
    app.state.log_path = Path(log_path)

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return DESK_PAGE

    @app.get("/v1/desk")
    def desk() -> dict[str, Any]:
        return read_desk(app.state.log_path)

    return app


def read_desk(path: str | Path, *, limit: int = 40) -> dict[str, Any]:
    """Newest evaluation first. A broken line is skipped and the earlier lines stay."""
    rows = _rows(Path(path))
    history = [_view(row) for row in reversed(rows[-limit:])]
    return {
        "pair": "EURGBP",
        "log": str(path),
        "count": len(rows),
        "latest": history[0] if history else None,
        "history": history,
        "last_order": _last_order(Path(path)),
    }


def _last_order(path: Path) -> dict[str, Any] | None:
    rows = _rows(path.with_name("orders.jsonl"))
    if not rows:
        return None
    latest = rows[-1]
    return {
        "action": latest.get("action"),
        "live_order": latest.get("live_order"),
        "order_sent": bool(latest.get("order_sent")),
        "decision": latest.get("decision"),
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _view(row: dict[str, Any]) -> dict[str, Any]:
    inputs = _mapping(row.get("inputs"))
    rates = _mapping(inputs.get("rates"))
    return {
        "ingested_at": row.get("ingested_at"),
        "mode": row.get("mode"),
        "decision": row.get("decision"),
        "assessment": assessment(row),
        "status": row.get("status"),
        "direction": row.get("direction"),
        "reasons": _strings(row.get("reasons")),
        "narratives": _strings(row.get("narratives")),
        "model_readiness": row.get("model_readiness"),
        "event_pressure": _number(row.get("event_pressure")),
        "fair_value": _number(inputs.get("fair_value")),
        "gap_pips": _gap(_quote(inputs.get("quote")), _number(inputs.get("fair_value"))),
        "quote": _quote(inputs.get("quote")),
        "rates": {
            "uk2y": _number(rates.get("uk2y")),
            "de2y": _number(rates.get("de2y")),
            "rate_diff": _number(rates.get("rate_diff")),
            "uk2y_time": rates.get("uk2y_time"),
            "de2y_time": rates.get("de2y_time"),
        },
        "headlines": _headlines(inputs.get("information")),
        "errors": _errors(inputs),
        "executed": bool(row.get("executed")),
        "live_order": row.get("live_order"),
        "duplicate": bool(row.get("duplicate")),
        "opportunity_id": row.get("opportunity_id"),
    }


def _gap(quote: dict[str, float] | None, fair_value: float | None) -> float | None:
    if quote is None or fair_value is None:
        return None
    return round((quote["mid"] - fair_value) / PIP, 2)


def _quote(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    bid = _number(raw.get("bid"))
    ask = _number(raw.get("ask"))
    if bid is None or ask is None:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0, "spread_pips": round((ask - bid) / PIP, 6)}


def _headlines(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    prints = [item for item in raw if isinstance(item, dict) and isinstance(item.get("headline"), str)]
    carrying = [item for item in prints if _number(item.get("surprise")) not in {None, 0.0}]
    if not carrying:
        carrying = [
            item
            for item in prints
            if item.get("kind") == "CALENDAR" and item.get("country") in {"UK", "EZ"}
        ]
    if not carrying:
        carrying = [item for item in prints if item.get("kind") != "NEWS"]
    return [
        {
            "headline": item["headline"],
            "country": item.get("country"),
            "actual": _number(item.get("actual")),
            "forecast": _number(item.get("forecast")),
            "surprise": _number(item.get("surprise")),
        }
        for item in carrying[:8]
    ]


def _errors(inputs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("information_error", "rate_error", "quote_error"):
        value = inputs.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    return found


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


DESK_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EURGBP 相对价值</title>
<style>
  :root {
    --paper: #f3efe6;
    --card: #fbf8f2;
    --ink: #1d1a16;
    --muted: #6d655c;
    --line: #ddd4c6;
    --watch: #8a5a12;
    --long: #1d6b45;
    --short: #8e3030;
    --degraded: #5e584f;
    --signal: #1d4e8f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font: 15px/1.45 "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
  }
  main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 48px; }
  header { display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 18px; }
  .kicker { margin: 0; letter-spacing: 0.14em; font-size: 12px; color: var(--muted); }
  h1 { margin: 2px 0 0; font: 28px/1.1 "Palatino Linotype", Palatino, "Songti SC", serif; font-weight: 600; }
  #meta { margin: 0; color: var(--muted); text-align: right; }
  .call, .panel { background: var(--card); border: 1px solid var(--line); border-radius: 8px; }
  .call { padding: 22px 24px 18px; margin-bottom: 14px; }
  .decision { margin: 0; font: 64px/0.95 "Palatino Linotype", Palatino, serif; letter-spacing: -0.03em; }
  .decision.WATCH { color: var(--watch); }
  .decision.LONG { color: var(--long); }
  .decision.SHORT { color: var(--short); }
  .decision.DATA_DEGRADED { color: var(--degraded); }
  .assessment { margin: 8px 0 0; font-size: 13px; letter-spacing: 0.12em; color: var(--muted); }
  .assessment.SIGNAL, .assessment.REVERSAL { color: var(--signal); }
  .reasons { margin: 12px 0 0; padding: 0; list-style: none; color: var(--muted); }
  .grid { display: grid; grid-template-columns: 1.3fr 0.7fr; gap: 14px; }
  .panel { padding: 16px 18px 14px; margin-bottom: 14px; }
  .panel h2 { margin: 0 0 10px; font-size: 13px; font-weight: 650; letter-spacing: 0.04em; color: var(--muted); }
  .figures { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .figures b {
    display: block;
    font: 22px/1.1 "Palatino Linotype", Palatino, serif;
    font-variant-numeric: tabular-nums;
  }
  .figures span { color: var(--muted); font-size: 12px; }
  svg { width: 100%; height: 140px; display: block; }
  .axis { fill: var(--muted); font-size: 11px; }
  ul.clean { margin: 0; padding-left: 18px; }
  ul.clean li { margin: 4px 0; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-size: 12px; font-weight: 650; }
  .empty { color: var(--muted); margin: 0; }
  code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 13px; }
  @media (max-width: 800px) {
    .grid, .figures { grid-template-columns: 1fr 1fr; }
    header { display: block; }
    #meta { text-align: left; margin-top: 8px; }
    .decision { font-size: 48px; }
  }
</style>
</head>
<body>
<main id="desk">
  <header>
    <div>
      <p class="kicker">EURGBP</p>
      <h1>相对价值监控</h1>
    </div>
    <p id="meta">正在读取记录</p>
  </header>
  <section class="call" id="call"></section>
  <div class="grid">
    <section class="panel" id="book"></section>
    <section class="panel" id="rates"></section>
  </div>
  <section class="panel" id="chart"></section>
  <div class="grid">
    <section class="panel" id="story"></section>
    <section class="panel" id="prints"></section>
  </div>
  <section class="panel" id="history"></section>
</main>
<script>
const SVG = "http://www.w3.org/2000/svg";

function el(name, text) {
  const node = document.createElement(name);
  if (text != null) node.textContent = text;
  return node;
}

function money(value) {
  return value == null ? "—" : Number(value).toFixed(5);
}

function plain(value, digits) {
  return value == null ? "—" : Number(value).toFixed(digits);
}

function render(body) {
  const latest = body.latest;
  document.getElementById("meta").textContent = latest
    ? "共 " + body.count + " 次评估 · 最近 " + latest.ingested_at + " · 每 5 秒刷新 · 不下单"
    : "记录还是空的 · 每 5 秒刷新";
  drawCall(latest, body.last_order);
  drawBook(latest);
  drawRates(latest);
  drawChart(body.history);
  drawStory(latest);
  drawPrints(latest);
  drawHistory(body.history);
}

function drawCall(latest, lastOrder) {
  const root = document.getElementById("call");
  root.replaceChildren();
  if (!latest) {
    const note = el("p", "还没有评估。另开一个终端运行 uv run rv watch，下一次判断会出现在这里。");
    note.className = "empty";
    root.append(note);
    return;
  }
  const word = el("p", latest.decision || "—");
  word.className = "decision " + (latest.decision || "");
  const mark = el("p", (latest.assessment || "UPDATE") + " · " + (latest.mode || ""));
  mark.className = "assessment " + (latest.assessment || "");
  const reasons = el("ul");
  reasons.className = "reasons";
  (latest.reasons.length ? latest.reasons : ["没有附加原因"]).forEach((reason) => reasons.append(el("li", reason)));
  root.append(word, mark, reasons);
  latest.errors.forEach((error) => root.append(el("p", error)));
  if (lastOrder && lastOrder.live_order) {
    root.append(el("p", "最近订单 " + lastOrder.action + " · " + lastOrder.live_order));
  }
}

function drawBook(latest) {
  const root = document.getElementById("book");
  root.replaceChildren(el("h2", "报价"));
  const quote = latest && latest.quote;
  const grid = el("div");
  grid.className = "figures";
  const cells = [
    ["买价", quote && money(quote.bid)],
    ["卖价", quote && money(quote.ask)],
    ["中间价", quote && money(quote.mid)],
    ["点差", quote ? plain(quote.spread_pips, 1) + " pip" : "—"],
    ["公允价值", latest && latest.fair_value != null ? money(latest.fair_value) : "—"],
    ["偏离", latest && latest.gap_pips != null ? plain(latest.gap_pips, 1) + " pip" : "—"]
  ];
  cells.forEach((pair) => {
    const cell = el("div");
    cell.append(el("span", pair[0]), el("b", pair[1]));
    grid.append(cell);
  });
  if (latest && latest.fair_value != null) {
    root.append(el("p", "由英国2年期减德国2年期及其20日变化拟合。政策、增长、通胀和风险不在这次数里。"));
  }
  root.append(grid);
}

function drawRates(latest) {
  const root = document.getElementById("rates");
  root.replaceChildren(el("h2", "同日利率"));
  const rates = latest ? latest.rates : {};
  const grid = el("div");
  grid.className = "figures";
  const cells = [
    ["英国2年", plain(rates.uk2y, 3)],
    ["德国2年", plain(rates.de2y, 3)],
    ["利差", plain(rates.rate_diff, 3)],
    ["因子", latest && latest.event_pressure != null ? plain(latest.event_pressure, 2) : "—"]
  ];
  cells.forEach((pair) => {
    const cell = el("div");
    cell.append(el("span", pair[0]), el("b", pair[1]));
    grid.append(cell);
  });
  root.append(grid);
}

function drawChart(history) {
  const root = document.getElementById("chart");
  root.replaceChildren(el("h2", "已记录的中间价"));
  const points = history.filter((row) => row.quote).slice().reverse();
  if (!points.length) {
    root.append(el("p", "还没有报价。"));
    return;
  }
  const values = points.map((row) => row.quote.mid);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 0.0001;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 640 140");
  const step = points.length === 1 ? 0 : 600 / (points.length - 1);
  const coords = values.map((value, index) => {
    const x = 24 + index * step;
    const y = 16 + (1 - (value - low) / span) * 96;
    return [x, y];
  });
  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("points", coords.map((pair) => pair.join(",")).join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "#1d1a16");
  line.setAttribute("stroke-width", "2");
  svg.append(line);
  coords.forEach((pair) => {
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("cx", pair[0]);
    dot.setAttribute("cy", pair[1]);
    dot.setAttribute("r", "3");
    dot.setAttribute("fill", "#1d1a16");
    svg.append(dot);
  });
  const lowLabel = document.createElementNS(SVG, "text");
  lowLabel.setAttribute("x", "8");
  lowLabel.setAttribute("y", "124");
  lowLabel.setAttribute("class", "axis");
  lowLabel.textContent = low.toFixed(5);
  const highLabel = document.createElementNS(SVG, "text");
  highLabel.setAttribute("x", "8");
  highLabel.setAttribute("y", "14");
  highLabel.setAttribute("class", "axis");
  highLabel.textContent = high.toFixed(5);
  svg.append(lowLabel, highLabel);
  root.append(svg);
}

function drawStory(latest) {
  const root = document.getElementById("story");
  root.replaceChildren(el("h2", "因子"));
  if (!latest) {
    root.append(el("p", "等待第一次评估。"));
    return;
  }
  if (!latest.narratives.length) {
    const note = latest.fair_value == null
      ? "公允价值还没算出来。利率历史或欧元兑英镑日线不够 252 个交易日时，这里继续观望。"
      : "这次没有相对因子。";
    root.append(el("p", note));
    return;
  }
  const list = el("ul");
  list.className = "clean";
  latest.narratives.forEach((line) => list.append(el("li", line)));
  root.append(list);
}

function drawPrints(latest) {
  const root = document.getElementById("prints");
  root.replaceChildren(el("h2", "消息"));
  if (!latest) {
    root.append(el("p", "等待第一次评估。"));
    return;
  }
  if (!latest.headlines.length) {
    root.append(el("p", "这次没有可用的意外差。"));
    return;
  }
  const list = el("ul");
  list.className = "clean";
  latest.headlines.forEach((item) => {
    const suffix = item.surprise == null ? "" : "  实际 " + item.actual + " / 预期 " + item.forecast;
    list.append(el("li", item.headline + suffix));
  });
  root.append(list);
}

function drawHistory(history) {
  const root = document.getElementById("history");
  root.replaceChildren(el("h2", "评估记录"));
  if (!history.length) return;
  const table = el("table");
  const head = el("tr");
  ["时间", "判断", "标记", "买价 / 卖价", "原因"].forEach((name) => head.append(el("th", name)));
  table.append(head);
  history.forEach((row) => {
    const line = el("tr");
    const quote = row.quote ? money(row.quote.bid) + " / " + money(row.quote.ask) : "—";
    [row.ingested_at, row.decision, row.assessment, quote, (row.reasons || []).join("；")].forEach((value) => {
      line.append(el("td", value || "—"));
    });
    table.append(line);
  });
  root.append(table);
}

async function load() {
  const response = await fetch("/v1/desk", {cache: "no-store"});
  render(await response.json());
}

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""

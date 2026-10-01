"""Research API. It reads the same point-in-time store as the CLI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from api.service import EngineService
from backtest.engine import run_backtest
from domain.errors import UnsupportedPairError
from domain.models import require_eurgbp


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime


def create_app(service: EngineService) -> FastAPI:
    app = FastAPI(title="Relative Value Opportunity Engine", version="0.1.0")
    app.state.service = service
    app.state.opportunities = {}
    app.state.backtests = {}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, bool]:
        if not service.ready():
            raise HTTPException(status_code=503, detail="market store is empty")
        return {"ready": True}

    @app.get("/v1/pairs/{pair}/mechanisms")
    def mechanisms(pair: str) -> dict[str, Any]:
        _pair(pair)
        return {"pair": "EURGBP", "mechanisms": service.mechanisms()}

    @app.get("/v1/pairs/{pair}/factors")
    def factors(pair: str) -> dict[str, Any]:
        _pair(pair)
        return service.factors()

    @app.get("/v1/pairs/{pair}/expectations")
    def expectations(pair: str) -> dict[str, Any]:
        _pair(pair)
        return service.expectations()

    @app.get("/v1/pairs/{pair}/fair-value")
    def fair_value(pair: str) -> dict[str, Any]:
        _pair(pair)
        return service.fair_value()

    @app.get("/v1/pairs/{pair}/regime")
    def regime(pair: str) -> dict[str, Any]:
        _pair(pair)
        return service.regime()

    @app.get("/v1/pairs/{pair}/state")
    def state(pair: str) -> dict[str, Any]:
        _pair(pair)
        return {
            "factors": service.factors(),
            "fair_value": service.fair_value(),
            "regime": service.regime(),
        }

    @app.get("/v1/pairs/{pair}/alpha-health")
    def alpha_health(pair: str) -> dict[str, Any]:
        _pair(pair)
        return {
            "pair": "EURGBP",
            "lifecycle": "DISCOVERY",
            "note": "A single snapshot cannot promote alpha. POST /v1/backtests for the health path.",
            "mechanism_state": service.mechanism_state(),
        }

    @app.get("/v1/pairs/{pair}/signal")
    def signal(pair: str) -> dict[str, Any]:
        _pair(pair)
        value = service.fair_value()
        residual = value.get("residual")
        direction = None
        if isinstance(residual, float):
            direction = "SHORT" if residual > 0 else "LONG" if residual < 0 else None
        return {
            "pair": "EURGBP",
            "direction": direction,
            "residual": residual,
            "source": "fair_value_residual",
            "note": "A signal is not an opportunity. The decision gates still have to pass.",
        }

    @app.get("/v1/opportunities")
    def opportunities() -> dict[str, Any]:
        payload = _remember_opportunity(app, service)
        return {"opportunities": [payload]}

    @app.get("/v1/opportunities/{opportunity_id}")
    def opportunity(opportunity_id: str) -> dict[str, Any]:
        found = app.state.opportunities.get(opportunity_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown opportunity")
        return found

    @app.post("/v1/backtests")
    def create_backtest(body: BacktestRequest) -> dict[str, Any]:
        try:
            result = run_backtest(service.store, service.config, body.start, body.end)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        payload = result.run.model_dump(mode="json")
        payload["decision_counts"] = result.decision_counts
        app.state.backtests[result.run.run_id] = payload
        return payload

    @app.get("/v1/backtests/{run_id}")
    def get_backtest(run_id: str) -> dict[str, Any]:
        found = app.state.backtests.get(run_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown backtest")
        return found

    return app


def _remember_opportunity(app: FastAPI, service: EngineService) -> dict[str, Any]:
    try:
        opportunity = service.current_opportunity()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload = opportunity.model_dump(mode="json")
    app.state.opportunities[opportunity.opportunity_id] = payload
    return payload


def _pair(pair: str) -> None:
    try:
        require_eurgbp(pair)
    except UnsupportedPairError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

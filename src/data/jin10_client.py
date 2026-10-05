"""Jin10 MCP client. The token stays in the environment."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_MCP_URL = "https://mcp.jin10.com/mcp"
PROTOCOL_VERSION = "2025-11-25"


class Jin10Error(RuntimeError):
    """Jin10 refused the call or the payload could not be read."""


class Jin10Client:
    """Same MCP session shape used by the local gold-signal client."""

    def __init__(self, token: str, url: str = DEFAULT_MCP_URL, timeout: float = 30.0) -> None:
        if not token.strip():
            raise Jin10Error("JIN10_TOKEN is empty")
        self._token = token.strip()
        self._url = url.rstrip("/")
        self._http = httpx.Client(timeout=timeout)
        self._session_id: str | None = None
        self._request_id = 0
        self._connected = False
        self._tools: set[str] = set()

    def close(self) -> None:
        self._http.close()

    def quote(self, code: str) -> dict[str, Any]:
        payload = self.call_tool("get_quote", {"code": code})
        data = _unwrap(payload)
        if not isinstance(data, dict) or data.get("close") is None:
            raise Jin10Error(f"Jin10 has no quote for {code}")
        return data

    def kline(self, code: str, count: int) -> list[Any]:
        payload = self.call_tool("get_kline", {"code": code, "count": count})
        return _extract_list(payload, ("klines", "items", "list", "bars", "data"))

    def calendar(self) -> list[dict[str, Any]]:
        return self._listed("list_calendar", ("data", "items", "list", "calendar"))

    def flash(self) -> list[dict[str, Any]]:
        return self._listed("list_flash", ("data", "items", "list", "flash"))

    def news(self) -> list[dict[str, Any]]:
        return self._listed("list_news", ("data", "items", "list", "news"))

    def _listed(self, tool: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        payload = self.call_tool(tool, {})
        rows = _extract_list(payload, keys)
        return [row for row in rows if isinstance(row, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self._ensure_connected()
        if name not in self._tools:
            known = ", ".join(sorted(self._tools))
            raise Jin10Error(f"Jin10 MCP has no tool {name}. Available: {known}")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise Jin10Error(f"Jin10 tool {name} failed")
        return _primary(result)

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "relative-value-engine", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, expect_response=False)
        listed = self._rpc("tools/list", {})
        tools = listed.get("tools") or []
        self._tools = {str(tool["name"]) for tool in tools if isinstance(tool, dict) and tool.get("name")}
        self._connected = True

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        expect_response: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if expect_response:
            self._request_id += 1
            body["id"] = self._request_id
        try:
            response = self._http.post(self._url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise Jin10Error(f"Jin10 network error calling {method}") from exc
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if not expect_response:
            if response.status_code not in {200, 202, 204}:
                raise Jin10Error(f"Jin10 {method} failed with HTTP {response.status_code}")
            return {}
        if response.status_code >= 400:
            raise Jin10Error(f"Jin10 {method} failed with HTTP {response.status_code}")
        payload = _parse_response(response)
        if payload.get("error"):
            message = payload["error"].get("message") if isinstance(payload["error"], dict) else payload["error"]
            raise Jin10Error(f"Jin10 JSON-RPC error on {method}: {message}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise Jin10Error(f"Jin10 {method} returned no object result")
        return result


def token_from_env() -> str:
    token = os.environ.get("JIN10_TOKEN", "").strip()
    if token:
        return token
    raise Jin10Error("JIN10_TOKEN is not set. Jin10 ingest does not fall back to the laboratory.")


def mcp_url_from_env() -> str:
    return os.environ.get("JIN10_MCP_URL", DEFAULT_MCP_URL).strip() or DEFAULT_MCP_URL


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    text = response.text
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type or text.lstrip().startswith("event:"):
        message = _sse_message(text)
        if not isinstance(message, dict):
            raise Jin10Error("Jin10 SSE payload is not an object")
        return message
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise Jin10Error("Jin10 returned non-JSON") from exc
    if not isinstance(data, dict):
        raise Jin10Error("Jin10 JSON is not an object")
    return data


def _sse_message(text: str) -> Any:
    event_name = ""
    data_lines: list[str] = []
    messages: list[Any] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = ""
            return
        messages.append(json.loads("\n".join(data_lines)))
        event_name = ""
        data_lines = []

    for line in text.splitlines():
        if line == "":
            flush()
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    if not messages:
        raise Jin10Error("Jin10 SSE had no data")
    _ = event_name
    return messages[-1]


def _primary(result: dict[str, Any]) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    return item["text"]
    return result


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_list(value, keys)
            if nested:
                return nested
    return []

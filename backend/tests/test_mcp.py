"""The MCP server is a thin client of the engine API: same rules, same rights gate."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from autoshorts import mcp_server
from autoshorts.api.app import app

api = TestClient(app, base_url="http://localhost:3000")


@pytest.fixture
def engine(monkeypatch):
    """Send the MCP server's HTTP calls to the in-process engine."""
    def request(method, url, headers=None, timeout=None, **kw):
        return api.request(method, url.replace(mcp_server._base(), ""), headers=headers, **kw)
    monkeypatch.setattr(mcp_server.httpx, "request", request)


def test_tools_cover_the_workflow():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"status", "make_clips", "make_faceless_video", "list_clips", "approve_clips", "reject_clips"} <= names


def test_status_goes_through_the_api(engine):
    out = mcp_server.status()
    assert "review" in out["summary"] and out["processing"] == []


def test_rights_gate_applies(engine):
    with pytest.raises(mcp_server.ToolError, match="own this video or have permission"):
        mcp_server.make_clips("https://example.com/v", i_own_or_have_permission=False)


def test_engine_not_running(monkeypatch):
    def down(*a, **kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(mcp_server.httpx, "request", down)
    with pytest.raises(mcp_server.ToolError, match="autoshorts up"):
        mcp_server.list_clips()

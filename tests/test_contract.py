"""Tests for the contract the DevOps layer depends on. If these pass, your
probes, ServiceMonitor, SLO queries and Rollouts analysis will work.
"""
import pytest
from fastapi.testclient import TestClient

from libs.common import Settings
from libs.common.app import make_app


def _app(checks=None):
    return make_app(Settings(service_name="test-svc", drain_seconds=0), checks or {})


def test_healthz_ignores_dependencies():
    """LIVENESS must not depend on anything external. If it did, one DB blip
    would restart every pod at once."""
    async def broken():
        raise RuntimeError("database is down")

    with TestClient(_app({"db": broken})) as c:
        assert c.get("/healthz").status_code == 200      # still alive
        assert c.get("/readyz").status_code == 503       # but not ready


def test_readyz_reports_which_check_failed():
    async def broken():
        raise RuntimeError("connection refused")

    with TestClient(_app({"redis": broken})) as c:
        body = c.get("/readyz").json()
        assert body["ready"] is False
        assert "redis" in body["failed"]


def test_readyz_passes_when_deps_healthy():
    async def ok():
        return None

    with TestClient(_app({"db": ok})) as c:
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["ready"] is True


def test_request_id_is_echoed_and_generated():
    with TestClient(_app()) as c:
        assert c.get("/healthz", headers={"x-request-id": "abc123"}).headers["x-request-id"] == "abc123"
        assert len(c.get("/healthz").headers["x-request-id"]) == 16


def test_metrics_exposes_the_slo_histogram():
    app = _app()

    @app.get("/thing/{tid}")
    async def thing(tid: str):
        return {"id": tid}

    with TestClient(app) as c:
        c.get("/thing/1")
        c.get("/thing/2")
        body = c.get("/metrics").text

    assert "http_request_duration_seconds_bucket" in body
    assert 'le="0.3"' in body, "SLO bucket at 300ms must exist"
    # ROUTE TEMPLATE, not the raw path -- otherwise cardinality explodes.
    assert 'path="/thing/{tid}"' in body
    assert 'path="/thing/1"' not in body


def test_probes_are_not_counted_as_traffic():
    """Probe traffic would dominate your request rate and error budget."""
    with TestClient(_app()) as c:
        c.get("/healthz")
        assert 'path="/healthz"' not in c.get("/metrics").text

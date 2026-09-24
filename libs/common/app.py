"""App factory: metrics, request-id propagation, probes, graceful drain.

Every service is built with make_app(). That is deliberate: the DevOps layer
(ServiceMonitor, SLO queries, Argo Rollouts AnalysisTemplate, Loki correlation)
depends on all 8 services exposing the SAME metric names and probe semantics.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from libs.common import Settings, request_id_var, setup_logging

# --- Metrics contract -------------------------------------------------------
# `path` is the ROUTE TEMPLATE ("/products/{pid}"), never the raw URL. Using the
# raw path would put an unbounded value in a label and multiply your time series
# by the number of distinct product ids -> Prometheus OOM.
REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "path", "status"])
# Buckets are clustered around the 300ms latency SLO. Default buckets jump
# 0.25 -> 0.5 -> 1.0, which makes a p95 near 300ms pure interpolation guesswork.
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["service", "method", "path"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.15,
        0.2,
        0.25,
        0.3,
        0.4,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
    ),
)
INFLIGHT = Gauge("http_requests_inflight", "In-flight requests", ["service"])
READY = Gauge("service_ready", "1 if /readyz passes", ["service"])

ReadinessCheck = Callable[[], Awaitable[None]]


def make_app(
    settings: Settings,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
    on_startup: Callable[[], Awaitable[None]] | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    log = setup_logging(settings)
    checks = readiness_checks or {}
    state = {"ready": False, "draining": False}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if on_startup:
            await on_startup()
        state["ready"] = True
        log.info("started", port=settings.port)
        yield
        # SIGTERM lands here (uvicorn traps it and runs lifespan shutdown).
        # Fail readiness FIRST so load balancers stop sending new work, then
        # keep serving in-flight requests for drain_seconds, then close deps.
        state["draining"] = True
        state["ready"] = False
        READY.labels(settings.service_name).set(0)
        log.info("draining", seconds=settings.drain_seconds)
        await asyncio.sleep(settings.drain_seconds)
        if on_shutdown:
            await on_shutdown()
        log.info("stopped")

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.log = log

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        path = request.url.path
        started = time.perf_counter()
        INFLIGHT.labels(settings.service_name).inc()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            log.exception("unhandled", path=path)
            response = JSONResponse({"detail": "internal error"}, status_code=500)
        finally:
            INFLIGHT.labels(settings.service_name).dec()

        # Route template, resolved after routing. Falls back to the literal path
        # only for unmatched requests (404s), which are bounded in practice.
        route = request.scope.get("route")
        tmpl = getattr(route, "path", path)
        elapsed = time.perf_counter() - started
        if tmpl not in ("/metrics", "/healthz", "/readyz"):
            REQUESTS.labels(settings.service_name, request.method, tmpl, str(status)).inc()
            LATENCY.labels(settings.service_name, request.method, tmpl).observe(elapsed)
        response.headers["x-request-id"] = rid
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> Response:
        """LIVENESS. Tests this process only -- never a dependency. If this
        checked the database, one DB blip would restart every pod at once and
        CrashLoopBackOff would stop them recovering."""
        return PlainTextResponse("ok")

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        """READINESS. Dependencies belong here. Returning 503 removes this pod
        from the Service's EndpointSlice without killing it."""
        if state["draining"] or not state["ready"]:
            READY.labels(settings.service_name).set(0)
            return JSONResponse({"ready": False, "reason": "draining"}, status_code=503)
        failed = {}
        for name, check in checks.items():
            try:
                await check()
            except Exception as exc:
                # Keep the exception TYPE, not just its message. A bare
                # str(exc) turns "socket.gaierror: [Errno -2]" into a string
                # that could have come from four different layers -- which is
                # exactly how a dependency failure becomes an hour of guessing.
                failed[name] = f"{type(exc).__name__}: {exc}"[:300]
                # And log the full traceback once, so the cause is recoverable
                # from logs rather than only from a probe response body.
                log.warning("readiness_check_failed", check=name, exc_info=True)
        READY.labels(settings.service_name).set(0 if failed else 1)
        if failed:
            return JSONResponse({"ready": False, "failed": failed}, status_code=503)
        return JSONResponse({"ready": True, "checks": list(checks)})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def run(app: FastAPI, settings: Settings) -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_config=None,
        # Must exceed the ALB idle timeout (60s) or the ALB reuses a
        # connection this app just closed -> intermittent 502s.
        timeout_keep_alive=75,
    )

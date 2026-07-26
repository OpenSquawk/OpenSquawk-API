"""FastAPI application entrypoint."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import ALLOWED_ORIGINS, FLOWS_DIR, LOG_LEVEL
from app.flow_loader import load_all_flows
from app.flow_validator import validate_flow
from app.routes.decision_routes import router as decision_router
from app.routes.flow_routes import router as flow_router
from app.routes.session_routes import router as session_router
from app.routes.tool_routes import router as tool_router

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status code, and duration."""

    SKIP_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000

        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        logger.log(
            level,
            "%s %s  →  %d  (%.0f ms)",
            request.method,
            request.url.path,
            response.status_code,
            ms,
        )
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Load and validate the flows before the first request is served."""
    _load_and_validate_flows()
    yield


def _load_and_validate_flows() -> None:
    if not FLOWS_DIR.exists():
        logger.warning("Flows directory '%s' does not exist — no flows loaded", FLOWS_DIR)
        return

    flows = load_all_flows(FLOWS_DIR)

    for slug, flow in flows.items():
        result = validate_flow(flow)
        for issue in result.issues:
            log = logger.error if issue.severity == "error" else logger.warning
            log("[flow=%s] %s: %s", slug, issue.severity.upper(), issue.message)
        if result.valid:
            logger.info("[flow=%s] validation passed", slug)
        else:
            logger.error("[flow=%s] validation FAILED — fix errors before use", slug)


app = FastAPI(
    title="OpenSquawk LiveATC API",
    description="PM radio training backend — Phase 1/2 (deterministic routing)",
    version="2.0.0",
    lifespan=lifespan,
)

# Middleware order matters: CORS first, logging second (so CORS headers are set
# before the response code is read by the logger).
app.add_middleware(RequestLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flow_router)
app.include_router(session_router)
app.include_router(decision_router)
app.include_router(tool_router)


@app.get("/")
def health():
    from app.flow_loader import get_all_flows
    return {
        "status": "ok",
        "loaded_flows": list(get_all_flows().keys()),
    }

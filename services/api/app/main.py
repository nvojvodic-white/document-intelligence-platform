import os

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth_routes import router as auth_router
from app.api.datasource_routes import router as datasource_router
from app.rag.routes import router as rag_router
from app.observability.tracing import setup_tracing
from app.observability.metrics import get_metrics
from app.middleware.auth import APIKeyMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

setup_tracing()

# The UI and the API are on different origins in compose (5173 vs 8000), so
# every browser call is cross-origin and needs CORS. Server-to-server callers
# never exercise this, which is exactly why it was missed until the UI was
# opened in a browser.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app = FastAPI(
    title="Document Intelligence Platform",
    description=(
        "Multi-tenant document intelligence: connect a datasource, sync a "
        "directory into your own knowledge base, and chat over it."
    ),
    version="0.1.0"
)

app.add_middleware(APIKeyMiddleware)

# Added last on purpose. Starlette inserts each new middleware at the front of
# the stack, so the last one added is the OUTERMOST - which is what lets a
# preflight be answered before the API key gate sees it. Browsers send OPTIONS
# without credentials, so a gate that ran first would reject every preflight
# and the UI would fail with an unexplained network error.
#
# Origins are listed explicitly rather than "*": the browser sends an
# Authorization header on every call, and an allowlist keeps that token from
# being offered to any page that asks.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

FastAPIInstrumentor.instrument_app(app)
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(datasource_router, prefix="/api/v1", tags=["datasources"])
app.include_router(rag_router, prefix="/api/v1/rag", tags=["chat"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    data, content_type = get_metrics()
    return Response(content=data, media_type=content_type)

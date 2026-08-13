import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_routes import router as auth_router
from app.api.datasource_routes import router as datasource_router
from app.middleware.auth import APIKeyMiddleware
from app.rag.routes import router as rag_router

# The UI and API sit on different origins in compose, so every browser call is
# cross-origin. Server-to-server callers never exercise this.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

app = FastAPI(
    title="Document Intelligence Platform",
    description=(
        "Multi-tenant document intelligence: connect a datasource, sync a "
        "directory into your own knowledge base, and chat over it."
    ),
    version="0.1.0",
)

app.add_middleware(APIKeyMiddleware)

# Added last, so it ends up outermost: Starlette pushes each new middleware to
# the front. Preflight has to be answered before the API key gate sees it.
# Origins are an allowlist because the UI sends a bearer token on every call.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(datasource_router, prefix="/api/v1", tags=["datasources"])
app.include_router(rag_router, prefix="/api/v1/rag", tags=["chat"])


@app.get("/health")
def health():
    return {"status": "ok"}

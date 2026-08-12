from fastapi import FastAPI, Response
from app.rag.routes import router as rag_router
from app.observability.tracing import setup_tracing
from app.observability.metrics import get_metrics
from app.middleware.auth import APIKeyMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

setup_tracing()

app = FastAPI(
    title="Document Intelligence Platform",
    description=(
        "Multi-tenant document intelligence: connect a datasource, sync a "
        "directory into your own knowledge base, and chat over it."
    ),
    version="0.1.0"
)

app.add_middleware(APIKeyMiddleware)
FastAPIInstrumentor.instrument_app(app)
app.include_router(rag_router, prefix="/api/v1/rag")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    data, content_type = get_metrics()
    return Response(content=data, media_type=content_type)

"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.auth.router import admin_router, oidc_router
from app.auth.router import router as auth_router
from app.core.cors import get_cors_settings
from app.core.logging_config import configure_logging
from app.core.telemetry import configure_telemetry
from app.generation.router import conversations_router
from app.generation.router import router as generation_router
from app.ingestion.router import documents_router
from app.ingestion.router import router as ingestion_router
from app.retrieval.router import router as retrieval_router

configure_logging()

app = FastAPI(title="Self-Hosted RAG Platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_settings().allowed_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
configure_telemetry(app)
app.mount("/metrics", make_asgi_app())
app.include_router(auth_router)
app.include_router(oidc_router)
app.include_router(admin_router)
app.include_router(ingestion_router)
app.include_router(documents_router)
app.include_router(retrieval_router)
app.include_router(generation_router)
app.include_router(conversations_router)

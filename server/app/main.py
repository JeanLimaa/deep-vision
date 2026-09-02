"""Ponto de entrada da API.

    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

O micro-framework de alto desempenho citado na Secao 5.3 do TCC e o FastAPI,
sobre uvicorn (ASGI). A escolha se justifica pelo modelo assincrono: o servidor
precisa receber video de forma continua, responder comandos e transmitir
eventos ao painel simultaneamente, sem que uma dessas tarefas bloqueie a outra.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_control, routes_ingest, routes_media
from app.container import build_container
from app.settings import Settings, get_settings

WEB_DIR = Path(__file__).parent / "web"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )
    # O uvicorn duplica o log de acesso de cada quadro; silenciado de proposito.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = build_container(settings)
        app.state.container = container
        await container.start()
        logging.getLogger(__name__).info(
            "Servidor pronto | detector=%s tts=%s stt=%s audio=%s",
            container.detector.name,
            container.tts.name,
            container.stt.name,
            ",".join(sink.name for sink in container.sinks),
        )
        try:
            yield
        finally:
            await container.stop()

    app = FastAPI(
        title="Sistema Assistivo para Deficientes Visuais",
        description=(
            "Servidor Edge-to-Cloud: recebe video e telemetria do ESP32-CAM, "
            "executa a deteccao de objetos e devolve o retorno sonoro."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_ingest.router)
    app.include_router(routes_control.router)
    app.include_router(routes_media.router)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()

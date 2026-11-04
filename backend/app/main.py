from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers.research import router as research_router


init_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Anvesha API",
        version="0.1.0",
        description="Local-first research orchestration backend",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "anvesha-backend"}

    app.include_router(research_router)

    return app


app = create_app()

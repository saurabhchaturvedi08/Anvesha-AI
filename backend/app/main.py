from fastapi import FastAPI

from app.database import init_db
from app.routers.research import router as research_router


init_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Anvesha API",
        version="0.1.0",
        description="Local-first research orchestration backend",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "anvesha-backend"}

    app.include_router(research_router)

    return app


app = create_app()

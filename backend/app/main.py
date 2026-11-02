from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="Anvesha API",
        version="0.1.0",
        description="Local-first research orchestration backend",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "anvesha-backend"}

    return app


app = create_app()

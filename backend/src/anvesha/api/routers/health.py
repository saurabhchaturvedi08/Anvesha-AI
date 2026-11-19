from fastapi import APIRouter, Response, status
from sqlalchemy.exc import SQLAlchemyError

from anvesha.api.deps import DatabaseDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(db: DatabaseDep, response: Response) -> dict[str, str]:
    try:
        await db.ping()
    except (SQLAlchemyError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "database": "unavailable"}
    return {"status": "ready", "database": "ok"}

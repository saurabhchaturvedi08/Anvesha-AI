from fastapi import FastAPI

from anvesha.tenancy import ensure_local_principal


async def test_local_principal_bootstrap_is_idempotent(app: FastAPI) -> None:
    async with app.state.db.session() as session:
        again = await ensure_local_principal(session, "LOCAL@anvesha.invalid")
    assert again == app.state.local_principal

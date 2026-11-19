from alembic import command

from tests.conftest import alembic_config


def test_models_match_migrations(database_url: str) -> None:
    """Fails if a model changed without a matching Alembic migration."""
    command.check(alembic_config(database_url))


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")

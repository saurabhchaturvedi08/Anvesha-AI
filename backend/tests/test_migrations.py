from alembic import command

from tests.conftest import alembic_config


def test_models_match_migrations(database_url: str) -> None:
    """Fails if a model changed without a matching Alembic migration."""
    command.check(alembic_config(database_url))


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_full_migrate_is_idempotent_and_records_the_job_queue_version(database_url: str) -> None:
    import psycopg

    from anvesha.migrate import migrate
    from tests.conftest import make_settings

    settings = make_settings(database_url=database_url)
    migrate(settings)  # already up to date: nothing to do, no errors
    with psycopg.connect(settings.psycopg_database_url) as connection:
        version = connection.execute(
            "SELECT version FROM schema_components WHERE name = 'procrastinate'"
        ).fetchone()
        checkpoints = connection.execute("SELECT to_regclass('public.checkpoints')").fetchone()
    assert version is not None and version[0].count(".") == 2
    assert checkpoints is not None and checkpoints[0] == "checkpoints"

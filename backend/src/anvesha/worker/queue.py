"""The job queue (Procrastinate, stored in Postgres) shared by the API and the worker.

- ``anvesha.run_research``: executes a run. Jobs for the same run never overlap (lock),
  and at most one waits in the queue at a time (queueing lock).
- ``anvesha.recover_runs`` (every minute): requeues jobs of workers that died, and runs
  whose worker stopped sending heartbeats.
"""

import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import procrastinate
from procrastinate import JobContext, RetryStrategy
from procrastinate.connector import BaseConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import func, select

from anvesha.config import Settings
from anvesha.db.models import Run
from anvesha.db.session import Database
from anvesha.graph.executor import RetryRun, RunExecutor

RESEARCH_QUEUE = "research"
MAINTENANCE_QUEUE = "maintenance"
RUN_TASK = "anvesha.run_research"
RECOVER_TASK = "anvesha.recover_runs"
# A worker that hasn't sent a heartbeat for this long is considered dead.
STALLED_WORKER_SECONDS = 120
# A run whose heartbeat is older than this (or queued that long without starting) is
# requeued. Comfortably above the executor's 30-second heartbeat.
STALE_RUN_SECONDS = 180

logger = logging.getLogger(__name__)


def run_lock(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}"


class JobQueue:
    def __init__(self, settings: Settings, connector: BaseConnector | None = None) -> None:
        self.settings = settings
        if connector is None:
            connector = procrastinate.PsycopgConnector(
                conninfo=settings.psycopg_database_url, min_size=1, max_size=4
            )
        self.app = procrastinate.App(connector=connector)
        retry = RetryStrategy(
            max_attempts=settings.run_max_attempts - 1,
            wait=20,
            linear_wait=40,
            retry_exceptions={RetryRun},
        )

        @self.app.task(name=RUN_TASK, queue=RESEARCH_QUEUE, pass_context=True, retry=retry)
        async def run_research(context: JobContext, run_id: str) -> None:
            executor: RunExecutor = context.additional_context["executor"]
            previous_attempts = context.job.attempts
            await executor.execute(
                uuid.UUID(run_id),
                final_attempt=previous_attempts + 1 >= settings.run_max_attempts,
            )

        @self.app.periodic(cron="* * * * *", periodic_id="recover-runs")
        @self.app.task(
            name=RECOVER_TASK,
            queue=MAINTENANCE_QUEUE,
            pass_context=True,
            queueing_lock=RECOVER_TASK,
        )
        async def recover_runs(context: JobContext, timestamp: int) -> None:
            db: Database = context.additional_context["db"]
            await self.recover(db)

        self._run_task = run_research

    async def enqueue_run(self, run_id: uuid.UUID) -> None:
        """Queue a run for the worker. Does nothing if a job for it is already waiting."""
        with contextlib.suppress(AlreadyEnqueued):
            await self._run_task.configure(
                lock=run_lock(run_id), queueing_lock=run_lock(run_id)
            ).defer_async(run_id=str(run_id))

    async def recover(self, db: Database) -> int:
        """Requeue work lost to crashed workers. Returns the number of runs requeued."""
        manager = self.app.job_manager
        for job in await manager.get_stalled_jobs(seconds_since_heartbeat=STALLED_WORKER_SECONDS):
            logger.warning("Retrying job %s (%s) from a stalled worker", job.id, job.task_name)
            await manager.retry_job(job)
        cutoff = datetime.now(UTC) - timedelta(seconds=STALE_RUN_SECONDS)
        async with db.session() as session:
            stale = (
                await session.scalars(
                    select(Run.id).where(
                        Run.status.in_(("queued", "running")),
                        func.coalesce(Run.heartbeat_at, Run.updated_at) < cutoff,
                    )
                )
            ).all()
        for run_id in stale:
            logger.warning("Requeueing run %s (no recent heartbeat)", run_id)
            await self.enqueue_run(run_id)
        return len(stale)

    def worker_options(self, additional_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "queues": [RESEARCH_QUEUE, MAINTENANCE_QUEUE],
            "concurrency": self.settings.worker_concurrency,
            "additional_context": additional_context,
            # Successful jobs are only noise; failed ones stay for inspection.
            "delete_jobs": "successful",
            "stalled_worker_timeout": STALLED_WORKER_SECONDS,
            # Runs resume from checkpoints, so a restart only costs the step in progress.
            "shutdown_graceful_timeout": 30,
        }

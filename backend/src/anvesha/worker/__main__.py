"""The research worker. Run ``python -m anvesha.worker`` (after ``python -m anvesha.migrate``)."""

import logging

from anvesha.config import get_settings
from anvesha.graph.checkpointer import open_checkpointer
from anvesha.graph.executor import RunExecutor
from anvesha.migrate import run_async
from anvesha.services import open_services
from anvesha.worker.queue import JobQueue

logger = logging.getLogger("anvesha.worker")


async def run_worker() -> None:
    settings = get_settings()
    async with open_services(settings) as services, open_checkpointer(settings) as checkpointer:
        executor = RunExecutor(services, checkpointer)
        queue = JobQueue(settings)
        async with queue.app.open_async():
            logger.info(
                "Worker ready: %d concurrent run(s), %d researcher(s) per run",
                settings.worker_concurrency,
                settings.run_max_parallel_researchers,
            )
            await queue.app.run_worker_async(
                **queue.worker_options({"executor": executor, "db": services.db})
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run_async(run_worker)


if __name__ == "__main__":
    main()

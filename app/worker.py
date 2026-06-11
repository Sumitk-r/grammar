import logging
import time

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, create_tables
from app.models import JobStatus, ScrapeJob
from app.services.job_processor import process_job


def next_job_id() -> str | None:
    with SessionLocal() as db:
        return db.scalar(
            select(ScrapeJob.id)
            .where(ScrapeJob.status == JobStatus.queued)
            .order_by(ScrapeJob.created_at)
            .limit(1)
        )


def run_worker(once: bool = False) -> None:
    create_tables()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.info("Worker started")
    while True:
        job_id = next_job_id()
        if job_id:
            process_job(job_id)
        elif once:
            return
        else:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_worker()


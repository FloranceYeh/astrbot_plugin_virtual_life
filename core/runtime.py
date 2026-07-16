from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SchedulerRuntime:
    def __init__(self, timezone: ZoneInfo):
        self.scheduler = AsyncIOScheduler(
            timezone=timezone,
            executors={"default": AsyncIOExecutor()},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
        )

    def start(self, generate_time: str, generate_callback) -> None:
        hour, minute = map(int, generate_time.split(":"))
        self.scheduler.add_job(
            generate_callback,
            "cron",
            hour=hour,
            minute=minute,
            id="pvd:daily-generation",
            replace_existing=True,
        )
        self.scheduler.start()

    def add_date_job(self, job_id: str, run_at: datetime, callback, *args, misfire_grace_time: int = 120) -> None:
        self.scheduler.add_job(
            callback,
            "date",
            run_date=run_at,
            args=list(args),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=max(1, misfire_grace_time),
        )

    def scheduled_jobs(self) -> list[tuple[str, datetime]]:
        jobs = []
        for job in self.scheduler.get_jobs():
            run_at = getattr(job, "next_run_time", None)
            if run_at is not None:
                jobs.append((job.id, run_at))
        return sorted(jobs, key=lambda item: (item[1], item[0]))

    def remove_prefix(self, prefix: str) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()

    def remove(self, job_id: str) -> None:
        job = self.scheduler.get_job(job_id)
        if job:
            job.remove()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

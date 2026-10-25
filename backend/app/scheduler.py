"""定时任务（M4）：cron 表达式驱动的调度器，触发时以"无人在场自动模式"跑完整任务流。

结果与普通任务一样推送到聊天流（前端打开即可回看）；高危子任务自动跳过。
管理走 REST：POST/GET/DELETE /api/schedules。
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .orchestration.orchestrator import Orchestrator
from .storage.db import Database

log = logging.getLogger("paa.scheduler")


class SchedulerManager:
    def __init__(self, orch: Orchestrator, db: Database) -> None:
        self._orch = orch
        self._db = db
        self._scheduler = AsyncIOScheduler(misfire_grace_time=60)  # 默认本地时区

    async def start(self) -> None:
        for row in await self._db.list_schedules():
            if not row["enabled"]:
                continue
            self._add_job(row["id"], row["cron"])
            log.info("定时任务已装载：%s（%s）", row["name"], row["cron"])
        self._scheduler.start()
        log.info("调度器已启动（%d 个定时任务）", len(self._scheduler.get_jobs()))

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def _add_job(self, schedule_id: int, cron: str) -> None:
        self._scheduler.add_job(
            self._fire,
            CronTrigger.from_crontab(cron),
            args=[schedule_id],
            id=f"schedule-{schedule_id}",
            replace_existing=True,
            max_instances=1,
        )

    async def _fire(self, schedule_id: int) -> None:
        row = await self._db.get_schedule(schedule_id)
        if not row or not row["enabled"]:
            return
        await self._db.touch_schedule(schedule_id)
        log.info("⏰ 定时任务触发：%s", row["name"])
        try:
            await self._orch.run_scheduled(row["name"], row["prompt"])
        except Exception:
            log.exception("定时任务执行异常：%s", row["name"])

    # ---- 管理（REST 调用）----

    async def create(self, name: str, prompt: str, cron: str) -> int:
        CronTrigger.from_crontab(cron)  # 校验，非法抛 ValueError
        schedule_id = await self._db.add_schedule(name, prompt, cron)
        self._add_job(schedule_id, cron)
        log.info("新增定时任务 %s（%s）", name, cron)
        return schedule_id

    async def remove(self, schedule_id: int) -> None:
        await self._db.remove_schedule(schedule_id)
        try:
            self._scheduler.remove_job(f"schedule-{schedule_id}")
        except Exception:
            pass  # job 不存在（未启动/已触发完）
        log.info("删除定时任务 %s", schedule_id)

    async def list(self) -> list[dict]:
        return await self._db.list_schedules()

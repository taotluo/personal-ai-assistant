"""助理后端入口：python -m app.main（或 uvicorn app.main:app --reload）。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .agents.coder import CoderAgent
from .agents.local import ShellAgent
from .agents.research import ResearchAgent
from .agents.writer import WriterAgent
from .config import Settings, settings as default_settings
from .llm import build_llm
from .orchestration.event_bus import EventBus
from .orchestration.orchestrator import Orchestrator
from .orchestration.registry import AgentRegistry
from .scheduler import SchedulerManager
from .server.executors import ExecutorManager
from .server.ws import router as ws_router
from .storage.db import Database

from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("paa.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s: Settings = app.state.settings
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(s.data_dir / "assistant.db")
    await db.init()
    bus = EventBus(persist=db.log_event)
    llm = build_llm(s)  # zhipu 缺 key 在这里快速失败
    registry = AgentRegistry()
    registry.register(ResearchAgent(llm))
    registry.register(WriterAgent(llm))
    executors = ExecutorManager()
    registry.register(CoderAgent(executors))
    registry.register(ShellAgent(executors))
    app.state.db = db
    app.state.bus = bus
    app.state.executors = executors
    app.state.orch = Orchestrator(llm, registry, bus, db, s)
    scheduler = SchedulerManager(app.state.orch, db)
    app.state.scheduler = scheduler
    await scheduler.start()
    log.info(
        "后端就绪 http://%s:%s（llm=%s，执行者=%s）",
        s.host, s.port, s.llm, registry.names(),
    )
    if s.token == "dev-token":
        log.warning("正在使用默认 token（dev-token），公网部署前请设置 PAA_TOKEN")
    yield
    scheduler.stop()
    await db.close()
    log.info("后端已关闭")


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="personal-ai-assistant", lifespan=lifespan)
    app.state.settings = settings or default_settings
    # 个人工具：允许前端开发服务器跨域访问 REST
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ws_router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/tasks")
    async def tasks():
        return await app.state.db.list_tasks()

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str):
        detail = await app.state.db.get_task_detail(task_id)
        if detail is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="task not found")
        return detail

    @app.get("/api/schedules")
    async def list_schedules():
        return await app.state.scheduler.list()

    @app.post("/api/schedules")
    async def create_schedule(body: dict):
        try:
            schedule_id = await app.state.scheduler.create(
                str(body.get("name", "")).strip(),
                str(body.get("prompt", "")).strip(),
                str(body.get("cron", "")).strip(),
            )
        except ValueError as err:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"cron 不合法：{err}")
        return {"id": schedule_id}

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int):
        await app.state.scheduler.remove(schedule_id)
        return {"ok": True}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=default_settings.host, port=default_settings.port)

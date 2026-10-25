"""M4：定时任务自动模式 / 技能沉淀闭环 / 任务详情回溯。"""

import asyncio

from app.config import Settings
from app.llm.mock import MockLLMClient
from app.orchestration.event_bus import EventBus
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.registry import AgentRegistry, AgentResult
from app.scheduler import SchedulerManager
from app.storage.db import Database


class OkAgent:
    def __init__(self, name: str):
        self.name = name
        self.description = name
        self.calls = 0

    async def run(self, subtask, ctx):
        self.calls += 1
        return AgentResult(ok=True, output=f"{self.name} 的结果", artifacts=[])


async def _make(tmp_path):
    settings = Settings(data_dir=tmp_path, llm="mock")
    db = Database(tmp_path / "t.db")
    await db.init()
    bus = EventBus(persist=db.log_event)
    reg = AgentRegistry()
    agents = [OkAgent("research"), OkAgent("writer")]
    for a in agents:
        reg.register(a)
    orch = Orchestrator(MockLLMClient(), reg, bus, db, settings)
    return orch, bus, db, agents


async def _drive(orch, bus, timeout=10):
    """以定时自动模式跑一个完整任务，跟随事件直到终态。"""
    q = bus.subscribe()
    runner = asyncio.create_task(
        orch.run_scheduled("每日简报", "帮我调研 AI Agent 趋势并沉淀技能简报")
    )
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=timeout)
        events.append(ev)
        if ev["type"] == "skill_suggestion":
            orch.resolve_skill_decision(ev["data"]["skill_id"], True)  # 批准入库
        elif ev["type"] == "task_status" and ev["data"]["status"] in ("done", "failed", "cancelled"):
            break
    await asyncio.wait_for(runner, timeout=timeout)
    return events


async def test_scheduled_auto_mode(tmp_path):
    """定时任务：方案自动批准、高危自动跳过、技能建议批准后入库。"""
    orch, bus, db, agents = await _make(tmp_path)
    try:
        events = await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        assert task.auto_approve is True
        assert task.subtask_results["s1"]["ok"]             # research 正常执行
        assert task.subtask_state["s2"].value == "skipped"  # s2 高危 → 自动跳过
        assert task.state.value == "done"
        assert any(e["type"] == "skill_suggestion" for e in events)
        await asyncio.sleep(0.1)  # 入库是异步的
        skills = await db.list_skills()
        assert skills and skills[0]["name"] == "mock 主题简报"
    finally:
        await db.close()


async def test_skills_injected_into_planner(tmp_path):
    """批准的技能会注入后续规划提示词。"""
    orch, bus, db, _ = await _make(tmp_path)
    try:
        await _drive(orch, bus)
        await asyncio.sleep(0.1)
        captured = {}
        orig = orch.assistant.make_plan

        async def spy(*a, **k):
            captured.update(k)
            return await orig(*a, **k)

        orch.assistant.make_plan = spy
        q = bus.subscribe()
        runner = asyncio.create_task(orch.handle_user_message("帮我调研另一个主题"))
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev["type"] == "plan_card":
                orch.resolve_plan_decision(ev["task_id"], "approve", None)
            elif ev["type"] == "confirm_request":
                orch.resolve_confirm(ev["data"]["confirm_id"], True)
            elif ev["type"] == "task_status" and ev["data"]["status"] in ("done", "failed"):
                break
        await asyncio.wait_for(runner, timeout=10)
        assert captured.get("skills"), "已入库技能未注入规划"
        assert captured["skills"][0]["name"] == "mock 主题简报"
    finally:
        await db.close()


async def test_schedule_crud(tmp_path):
    db = Database(tmp_path / "s.db")
    await db.init()
    (tmp_path / "sub").mkdir()
    orch, _, db2, _ = await _make(tmp_path / "sub")
    try:
        mgr = SchedulerManager(orch, db2)
        sid = await mgr.create("每日简报", "帮我总结今天的学习", "0 9 * * *")
        rows = await mgr.list()
        assert len(rows) == 1 and rows[0]["name"] == "每日简报"
        await mgr.remove(sid)
        assert not await mgr.list()
        # db 层独立验证
        sid2 = await db.add_schedule("测试", "p", "*/5 * * * *")
        detail = await db.get_schedule(sid2)
        assert detail and detail["cron"] == "*/5 * * * *"
    finally:
        await db.close()
        await db2.close()


async def test_task_detail(tmp_path):
    orch, bus, db, _ = await _make(tmp_path)
    try:
        await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        detail = await db.get_task_detail(task.id)
        assert detail and detail["status"] == "done"
        assert detail["plan"] and detail["subtasks"] and detail["messages"]
    finally:
        await db.close()

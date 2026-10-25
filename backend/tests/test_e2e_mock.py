"""端到端（mock LLM）：真实编排器 + 假执行 Agent，验证完整状态机流转。"""

import asyncio

from app.config import Settings
from app.llm.mock import MockLLMClient
from app.orchestration.event_bus import EventBus
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.registry import AgentRegistry, AgentResult
from app.storage.db import Database


class FakeAgent:
    def __init__(self, name: str):
        self.name = name
        self.description = name
        self.calls = 0

    async def run(self, subtask, ctx):
        self.calls += 1
        await ctx.emit({"note": f"{self.name} 正在处理"})
        return AgentResult(ok=True, output=f"{self.name} 的结果", artifacts=[])


async def _make(tmp_path):
    settings = Settings(data_dir=tmp_path, llm="mock")
    db = Database(tmp_path / "t.db")
    await db.init()
    bus = EventBus(persist=db.log_event)
    reg = AgentRegistry()
    agents = [FakeAgent("research"), FakeAgent("writer")]
    for a in agents:
        reg.register(a)
    orch = Orchestrator(MockLLMClient(), reg, bus, db, settings)
    return orch, bus, db, agents


async def _drive(orch, bus, plan_answer=True, confirm_answer=True, timeout=10):
    """跑一个完整任务，按预设答案响应确认，直到任务到终态。"""
    q = bus.subscribe()
    runner = asyncio.create_task(
        orch.handle_user_message("帮我调研一下多智能体框架并写成笔记")
    )
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=timeout)
        events.append(ev)
        if ev["type"] == "plan_card":
            orch.resolve_plan_decision(
                ev["task_id"], "approve" if plan_answer else "reject", None
            )
        elif ev["type"] == "confirm_request":
            orch.resolve_confirm(ev["data"]["confirm_id"], confirm_answer)
        elif ev["type"] == "task_status" and ev["data"]["status"] in ("done", "failed", "cancelled"):
            break
    await asyncio.wait_for(runner, timeout=timeout)
    return events


async def test_full_loop_approve(tmp_path):
    orch, bus, db, agents = await _make(tmp_path)
    try:
        events = await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        assert task.state.value == "done"
        assert task.subtask_results["s1"]["ok"]
        assert task.subtask_results["s2"]["ok"]
        assert any(e["type"] == "summary" for e in events)
        assert all(a.calls == 1 for a in agents)
    finally:
        await db.close()


async def test_reject_plan_runs_nothing(tmp_path):
    orch, bus, db, agents = await _make(tmp_path)
    try:
        await _drive(orch, bus, plan_answer=False)
        task = next(iter(orch.tasks.values()))
        assert task.state.value == "cancelled"
        assert all(a.calls == 0 for a in agents)
    finally:
        await db.close()


async def test_deny_risky_subtask_skips_it(tmp_path):
    orch, bus, db, agents = await _make(tmp_path)
    try:
        await _drive(orch, bus, confirm_answer=False)
        task = next(iter(orch.tasks.values()))
        assert task.state.value == "done"          # s1 成功即算 done
        assert task.subtask_results["s1"]["ok"]
        assert "s2" not in task.subtask_results     # 拒绝高危 → 跳过，无结果记录
        assert agents[1].calls == 0
    finally:
        await db.close()

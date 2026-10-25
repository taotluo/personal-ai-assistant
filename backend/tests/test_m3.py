"""M3：失败自动重试 / 审核环重跑 / 记忆复盘与检索。"""

import asyncio

from app.agents.assistant import AssistantAgent
from app.config import Settings
from app.llm.mock import MockLLMClient
from app.orchestration.event_bus import EventBus
from app.orchestration.orchestrator import Orchestrator
from app.orchestration.registry import AgentRegistry, AgentResult
from app.protocol.messages import Retrospect, ReviewReport, ReviewVerdict, RetryHint
from app.storage.db import Database


class FlakyAgent:
    """前 fail_times 次失败，之后成功。"""

    def __init__(self, name: str, fail_times: int):
        self.name = name
        self.description = name
        self.fail_times = fail_times
        self.calls = 0

    async def run(self, subtask, ctx):
        self.calls += 1
        if self.calls <= self.fail_times:
            return AgentResult(ok=False, error=f"模拟失败 #{self.calls}")
        return AgentResult(ok=True, output=f"{self.name} 的结果", artifacts=[])


async def _make(tmp_path, agents, settings_kwargs=None):
    settings = Settings(data_dir=tmp_path, llm="mock", **(settings_kwargs or {}))
    db = Database(tmp_path / "t.db")
    await db.init()
    bus = EventBus(persist=db.log_event)
    reg = AgentRegistry()
    for a in agents:
        reg.register(a)
    orch = Orchestrator(MockLLMClient(), reg, bus, db, settings)
    return orch, bus, db


async def _drive(orch, bus, timeout=10):
    """跑一个完整任务（自动批准方案与高危确认），返回全部事件。"""
    q = bus.subscribe()
    runner = asyncio.create_task(orch.handle_user_message("帮我调研多智能体框架并写成笔记"))
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=timeout)
        events.append(ev)
        if ev["type"] == "plan_card":
            orch.resolve_plan_decision(ev["task_id"], "approve", None)
        elif ev["type"] == "confirm_request":
            orch.resolve_confirm(ev["data"]["confirm_id"], True)
        elif ev["type"] == "task_status" and ev["data"]["status"] in ("done", "failed", "cancelled"):
            break
    await asyncio.wait_for(runner, timeout=timeout)
    return events


async def test_inline_retry_recovers(tmp_path):
    flaky = FlakyAgent("research", fail_times=1)
    orch, bus, db = await _make(tmp_path, [flaky, FlakyAgent("writer", 0)])
    try:
        events = await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        assert task.state.value == "done"
        assert flaky.calls == 2  # 失败 1 次后自动重试成功
        assert task.subtask_results["s1"]["ok"]
        assert any(
            e["type"] == "subtask_status" and e["data"].get("status") == "retrying"
            for e in events
        )
        # 复盘记忆已入库（mock 复盘产出一条 lesson；retrospect 事件在 done 之后发布）
        mems = await db.relevant_memories("调研", 5)
        assert mems and mems[0]["kind"] == "lesson"
    finally:
        await db.close()


async def test_retry_budget_exhausted_fails_task(tmp_path):
    always_fail = FlakyAgent("research", fail_times=99)
    writer = FlakyAgent("writer", 0)
    orch, bus, db = await _make(tmp_path, [always_fail, writer])
    try:
        events = await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        assert always_fail.calls == 3  # 首次 + 2 次重试（max_retries=2）
        assert task.subtask_results["s1"]["ok"] is False
        assert task.subtask_state["s2"].value == "skipped"  # 依赖失败被跳过
        assert task.state.value == "failed"
        assert any(e["type"] == "review_report" for e in events)
    finally:
        await db.close()


class ScriptedAssistant:
    """可控助理替身：review 按剧本返回，其余复用 mock 逻辑。"""

    def __init__(self, llm, review_reports):
        self._inner = AssistantAgent(llm)
        self.reports = list(review_reports)

    async def triage(self, text, history):
        return await self._inner.triage(text, history)

    async def make_plan(self, *a, **k):
        return await self._inner.make_plan(*a, **k)

    async def summarize(self, goal, results_block):
        return "（测试）总结"

    async def review(self, results_block):
        if self.reports:
            return self.reports.pop(0)
        return ReviewReport(verdicts=[], overall="无意见")

    async def retry_instruction(self, instruction, failure):
        return RetryHint(instruction="重写后的指令")

    async def retrospect(self, goal, results_block, summary):
        return Retrospect(memories=[])


async def test_review_loop_reruns_quality_failures(tmp_path):
    agent = FlakyAgent("research", 0)  # 执行总是"成功"
    orch, bus, db = await _make(tmp_path, [agent, FlakyAgent("writer", 0)])
    # 第一轮审核：s1 质量不合格 → 换指令重跑；第二轮：无意见 → 结束
    orch.assistant = ScriptedAssistant(
        orch.llm,
        [ReviewReport(verdicts=[ReviewVerdict(subtask_id="s1", passed=False, reason="内容太浅")])],
    )
    try:
        events = await _drive(orch, bus)
        task = next(iter(orch.tasks.values()))
        assert agent.calls == 2  # 初始执行 1 次 + 审核重跑 1 次
        assert task.subtask_results["s1"]["ok"]
        assert task.state.value == "done"
        assert task.instructions["s1"] == "重写后的指令"
        assert any(e["type"] == "review_report" for e in events)
    finally:
        await db.close()


async def test_memory_relevance_scoring(tmp_path):
    db = Database(tmp_path / "m.db")
    await db.init()
    try:
        await db.add_memories(
            [("lesson", "zcode 任务必须给绝对路径"), ("preference", "总结要简洁"), ("fact", "用户常用 GLM")],
            "t1",
        )
        rel = await db.relevant_memories("zcode 路径怎么给", 2)
        assert rel and rel[0]["kind"] == "lesson"  # 关键词命中优先
        fallback = await db.relevant_memories("毫无交集的查询xyzq", 2)
        assert 0 < len(fallback) <= 3  # 无命中回退到最近几条
    finally:
        await db.close()

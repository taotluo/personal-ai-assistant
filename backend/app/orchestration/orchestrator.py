"""编排核心：状态机驱动的 plan → confirm → execute → summarize 主循环。

M3 将在 review 节点接入逐项审核、失败自动换策略重试与记忆复盘
（见 docs/architecture.md 路线图）。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field

from ..agents.assistant import AssistantAgent
from ..config import Settings
from ..llm import BaseLLM
from ..protocol.messages import Plan, ReviewReport, Subtask
from ..storage.db import Database
from .event_bus import EventBus
from .registry import AgentContext, AgentRegistry, AgentResult
from .state_machine import SubtaskState, TaskState, transition

log = logging.getLogger("paa.orchestrator")


@dataclass
class Task:
    id: str
    title: str
    goal: str
    state: TaskState = TaskState.planning
    plan: Plan | None = None
    subtask_state: dict = field(default_factory=dict)
    subtask_results: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=dict)     # subtask_id -> 已用重试次数
    instructions: dict = field(default_factory=dict)  # subtask_id -> 当前生效指令（重试时被改写）
    auto_approve: bool = False                        # 定时任务：无人在场自动模式
    created_at: float = field(default_factory=time.time)


def validate_plan(plan: Plan, registry: AgentRegistry) -> str | None:
    """方案静态校验：执行者存在、id 唯一、无环、本机类执行者带 workdir。返回错误文案或 None。"""
    ids = [st.id for st in plan.subtasks]
    if not ids:
        return "方案没有任何子任务"
    if len(set(ids)) != len(ids):
        return "子任务 id 重复"
    known = set(registry.names())
    idset = set(ids)
    for st in plan.subtasks:
        if st.agent not in known:
            return f"子任务 {st.id} 使用了未注册的执行者：{st.agent}"
        agent = registry.get(st.agent)
        if getattr(agent, "needs_workdir", False) and not st.workdir:
            return f"子任务 {st.id}（{st.agent}）要在用户本机执行，必须提供 workdir（绝对路径）"
        for dep in st.depends_on:
            if dep == st.id:
                return f"子任务 {st.id} 依赖自身"
            if dep not in idset:
                return f"子任务 {st.id} 依赖不存在的 {dep}"
    # 依赖环检测（Kahn 拓扑排序）
    indeg = {i: 0 for i in ids}
    nxt: dict[str, list[str]] = {i: [] for i in ids}
    for st in plan.subtasks:
        for dep in st.depends_on:
            indeg[st.id] += 1
            nxt[dep].append(st.id)
    queue = [i for i, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nx in nxt[cur]:
            indeg[nx] -= 1
            if indeg[nx] == 0:
                queue.append(nx)
    if seen != len(ids):
        return "子任务依赖存在环"
    return None


class Orchestrator:
    def __init__(
        self,
        llm: BaseLLM,
        registry: AgentRegistry,
        bus: EventBus,
        db: Database,
        settings: Settings,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.bus = bus
        self.db = db
        self.settings = settings
        self.assistant = AssistantAgent(llm)
        self.tasks: dict[str, Task] = {}
        self._plan_futs: dict[str, asyncio.Future] = {}
        self._confirm_futs: dict[str, asyncio.Future] = {}
        self._skill_pending: dict[str, dict] = {}  # suggestion_id -> 候选（等用户批准）
        self._counter = itertools.count(1)

    # ---------- 对外入口 ----------

    async def handle_user_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        await self.db.add_message("user", text)
        try:
            history = await self.db.recent_messages(12)
            triage = await self.assistant.triage(text, history)
        except Exception as err:
            log.exception("分流失败")
            await self._publish("error", {"message": f"助理分析失败：{err}"})
            return
        if triage.kind == "chat":
            reply = triage.reply or "（助理没有给出回答）"
            await self.db.add_message("assistant", reply)
            await self._publish("message", {"role": "assistant", "text": reply})
            return
        await self.run_task(text)

    def resolve_plan_decision(self, task_id: str, decision: str, plan: Plan | None) -> None:
        """WS 收到 plan_decision 后调用，唤醒等待中的任务。"""
        fut = self._plan_futs.get(task_id)
        if fut and not fut.done():
            fut.set_result({"decision": decision, "plan": plan})

    def resolve_confirm(self, confirm_id: str, approved: bool, note: str = "") -> None:
        fut = self._confirm_futs.get(confirm_id)
        if fut and not fut.done():
            fut.set_result({"approved": approved, "note": note})

    def resolve_skill_decision(self, suggestion_id: str, approved: bool) -> None:
        """用户对技能建议的裁决（WS skill_decision 调用）。"""
        candidate = self._skill_pending.pop(suggestion_id, None)
        if candidate is None:
            return
        if not approved:
            log.info("用户忽略技能建议：%s", candidate["name"])
            return

        async def _save() -> None:
            try:
                await self.db.add_skill(
                    candidate["name"], candidate["description"],
                    candidate["plan_hint"], candidate.get("task_id"),
                )
                await self._publish(
                    "message",
                    {"role": "assistant", "text": f"🧩 技能已入库：{candidate['name']}——{candidate['description']}（之后的规划会参考它）"},
                )
            except Exception:
                log.exception("技能入库失败")

        asyncio.ensure_future(_save())

    async def run_scheduled(self, name: str, prompt: str) -> None:
        """定时任务入口：无人在场自动模式（方案自动批准、高危子任务自动跳过）。"""
        await self.db.add_message("user", f"[定时:{name}] {prompt}")
        await self.run_task(prompt, auto_approve=True)

    # ---------- 任务主循环 ----------

    async def run_task(self, text: str, auto_approve: bool = False) -> None:
        task = Task(
            id=f"t{next(self._counter)}-{uuid.uuid4().hex[:6]}",
            title=text[:60],
            goal=text,
            auto_approve=auto_approve,
        )
        self.tasks[task.id] = task
        await self.db.save_task(task)
        await self._publish("task_status", {"status": "planning", "title": task.title}, task.id)

        try:
            history = await self.db.recent_messages(10)
            memories = await self.db.relevant_memories(text, 8)
            skills = await self.db.list_skills(20)
            plan = await self.assistant.make_plan(
                text, history, self.registry.catalog(),
                memories=memories, skills=skills, unattended=task.auto_approve,
            )
        except Exception as err:
            await self._fail_task(task, f"规划失败：{err}")
            return

        err = self._validate_plan(plan)
        if err:
            await self._fail_task(task, f"助理产出的方案不合法：{err}")
            return

        task.plan = plan
        for st in plan.subtasks:
            task.subtask_state[st.id] = SubtaskState.queued
            task.instructions[st.id] = st.instruction
        task.state = transition(task.state, TaskState.pending_confirm)
        await self.db.save_task(task)
        await self.db.save_subtasks(task.id, plan.subtasks)
        # 先注册等待者再发布事件：订阅者收到卡片即可决议，不存在丢决议的竞态
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._plan_futs[task.id] = fut
        await self._publish("plan_card", plan.model_dump(), task.id)
        await self._publish(
            "message",
            {"role": "assistant", "text": f"我把需求拆成了 {len(plan.subtasks)} 个子任务，见方案卡片，请确认。"},
            task.id,
        )
        if task.auto_approve:
            await self._publish(
                "message",
                {"role": "assistant", "text": "⏰ 定时任务：无人在场，方案已自动批准（高危子任务会自动跳过）。"},
                task.id,
            )
            decision = {"decision": "approve", "plan": None}
            self._plan_futs.pop(task.id, None)
        else:
            try:
                decision = await fut
            finally:
                self._plan_futs.pop(task.id, None)

        if decision["decision"] == "reject":
            task.state = transition(task.state, TaskState.cancelled)
            await self.db.update_task_status(task)
            await self._publish("task_status", {"status": "cancelled"}, task.id)
            await self._publish(
                "message", {"role": "assistant", "text": "已按你的要求取消该任务。"}, task.id
            )
            return

        if decision["decision"] == "modify" and decision.get("plan") is not None:
            new_plan: Plan = decision["plan"]
            err = self._validate_plan(new_plan)
            if err:
                await self._fail_task(task, f"修改后的方案不合法：{err}（任务终止，请重新发起）")
                return
            task.plan = new_plan
            task.subtask_state = {st.id: SubtaskState.queued for st in new_plan.subtasks}
            task.subtask_results = {}
            task.instructions = {st.id: st.instruction for st in new_plan.subtasks}
            await self.db.save_task(task)
            await self.db.save_subtasks(task.id, new_plan.subtasks)
            await self._publish("plan_card", new_plan.model_dump(), task.id)

        task.state = transition(task.state, TaskState.running)
        await self.db.update_task_status(task)
        await self._publish("task_status", {"status": "running"}, task.id)

        await self._execute(task)

        # ---- 审核-重跑环（M3）：reviewing ⇄ running，不合格子任务换策略重跑 ----
        for _ in range(self.settings.max_review_rounds):
            task.state = transition(task.state, TaskState.reviewing)
            await self.db.update_task_status(task)
            await self._publish("task_status", {"status": "reviewing"}, task.id)
            report = await self._review(task)
            retry_ids = self._review_retry_ids(task, report) if report is not None else []
            if not retry_ids:
                break
            task.state = transition(task.state, TaskState.running)
            await self.db.update_task_status(task)
            await self._publish(
                "task_status",
                {"status": "running", "note": "审核未通过的子任务将换策略重跑"},
                task.id,
            )
            await self._requeue(task, retry_ids, report)
            await self._execute(task)
        if task.state is not TaskState.reviewing:
            task.state = transition(task.state, TaskState.reviewing)
            await self.db.update_task_status(task)
            await self._publish("task_status", {"status": "reviewing"}, task.id)

        try:
            summary = await self.assistant.summarize(task.plan.goal, self._result_block(task))
        except Exception:
            log.exception("总结生成失败，使用兜底汇总")
            summary = self._result_block(task)

        final = TaskState.done if self._has_success(task) else TaskState.failed
        task.state = transition(task.state, final)
        await self.db.update_task_status(task)
        await self._publish(
            "summary",
            {
                "text": summary,
                "subtasks": [
                    {
                        "id": st.id,
                        "title": st.title,
                        "ok": task.subtask_results.get(st.id, {}).get("ok", False),
                        "artifacts": task.subtask_results.get(st.id, {}).get("artifacts", []),
                    }
                    for st in task.plan.subtasks
                ],
            },
            task.id,
        )
        await self.db.add_message("assistant", summary, task.id)
        await self._retrospect(task, summary)  # 记忆沉淀在终态前发布，终态保持任务最后一条事件
        await self._publish("task_status", {"status": final.value}, task.id)

    # ---------- 子任务调度 ----------

    async def _execute(self, task: Task) -> None:
        assert task.plan is not None
        running: dict[str, asyncio.Task] = {}
        finished = {SubtaskState.succeeded, SubtaskState.failed, SubtaskState.skipped}
        while True:
            for st in task.plan.subtasks:
                if task.subtask_state.get(st.id) is not SubtaskState.queued:
                    continue
                deps = st.depends_on
                if not all(task.subtask_state.get(d) in finished for d in deps):
                    continue  # 依赖还没结束，下轮再看
                if any(task.subtask_state.get(d) is not SubtaskState.succeeded for d in deps):
                    task.subtask_state[st.id] = SubtaskState.skipped
                    await self.db.update_subtask(task.id, st.id, "skipped")
                    await self._publish(
                        "subtask_status",
                        {
                            "subtask_id": st.id,
                            "title": st.title,
                            "agent": st.agent,
                            "status": "skipped",
                            "note": "依赖未成功，已跳过",
                        },
                        task.id,
                    )
                    continue
                running[st.id] = asyncio.create_task(self._run_subtask(task, st))
            if not running:
                break
            done, _ = await asyncio.wait(set(running.values()), return_when=asyncio.FIRST_COMPLETED)
            for sid in [sid for sid, t in running.items() if t in done]:
                del running[sid]

    async def _run_subtask(self, task: Task, st: Subtask) -> None:
        if st.risky and task.auto_approve:
            task.subtask_state[st.id] = SubtaskState.skipped
            await self.db.update_subtask(task.id, st.id, "skipped")
            await self._publish(
                "subtask_status",
                {
                    "subtask_id": st.id,
                    "title": st.title,
                    "agent": st.agent,
                    "status": "skipped",
                    "note": "定时任务无人在场，高危子任务自动跳过",
                },
                task.id,
            )
            return
        if st.risky:
            confirm_id = f"c{next(self._counter)}-{uuid.uuid4().hex[:6]}"
            # 同样先注册等待者再发布确认请求
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._confirm_futs[confirm_id] = fut
            task.subtask_state[st.id] = SubtaskState.awaiting_confirm
            await self._publish(
                "confirm_request",
                {
                    "confirm_id": confirm_id,
                    "subtask_id": st.id,
                    "title": st.title,
                    "reason": f"子任务「{st.title}」被标记为高危操作，需要你确认后才会执行。",
                },
                task.id,
            )
            try:
                res = await fut
            finally:
                self._confirm_futs.pop(confirm_id, None)
            if not res.get("approved"):
                task.subtask_state[st.id] = SubtaskState.skipped
                await self.db.update_subtask(task.id, st.id, "skipped")
                await self._publish(
                    "subtask_status",
                    {
                        "subtask_id": st.id,
                        "title": st.title,
                        "agent": st.agent,
                        "status": "skipped",
                        "note": "你拒绝了高危确认",
                    },
                    task.id,
                )
                return

        task.subtask_state[st.id] = SubtaskState.running
        await self.db.update_subtask(task.id, st.id, "running")
        await self._publish(
            "subtask_status",
            {"subtask_id": st.id, "title": st.title, "agent": st.agent, "status": "running"},
            task.id,
        )

        # 执行 + 失败自动换策略重试（attempts=已消耗的重试次数，上限 max_retries；
        # 审核环的重试同样消耗该预算）
        while True:
            result = await self._execute_once(task, st)
            if result.ok:
                break
            if task.attempts.get(st.id, 0) >= self.settings.max_retries:
                break
            task.attempts[st.id] = task.attempts.get(st.id, 0) + 1
            failure = result.error or "未知失败"
            task.instructions[st.id] = await self._retry_instruction(task, st, failure)
            await self._publish(
                "subtask_status",
                {
                    "subtask_id": st.id,
                    "title": st.title,
                    "agent": st.agent,
                    "status": "retrying",
                    "attempt": task.attempts[st.id],
                    "note": failure[:200],
                },
                task.id,
            )
        await self._finish_subtask(task, st, result)

    async def _execute_once(self, task: Task, st: Subtask) -> AgentResult:
        """执行一轮子任务：用当前生效指令（可能是重试改写版）调用执行者。"""
        eff = task.instructions.get(st.id) or st.instruction
        sub = st if eff == st.instruction else st.model_copy(update={"instruction": eff})

        async def emit(data: dict) -> None:
            await self._publish(
                "subtask_status",
                {"subtask_id": st.id, "status": "progress", **data},
                task.id,
            )

        ctx = AgentContext(task_id=task.id, subtask=sub, emit=emit)
        try:
            agent = self.registry.get(st.agent)
        except KeyError:
            return AgentResult(ok=False, error=f"未注册的执行者：{st.agent}")
        try:
            return await asyncio.wait_for(agent.run(sub, ctx), timeout=self.settings.agent_timeout)
        except asyncio.TimeoutError:
            return AgentResult(ok=False, error=f"执行超时（>{self.settings.agent_timeout:.0f}s）")
        except Exception as exc:
            log.exception("子任务 %s 执行异常", st.id)
            return AgentResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def _retry_instruction(self, task: Task, st: Subtask, failure: str) -> str:
        eff = task.instructions.get(st.id) or st.instruction
        try:
            hint = await self.assistant.retry_instruction(eff, failure)
            return hint.instruction
        except Exception:
            log.exception("重试指令生成失败，使用兜底改写")
            return (
                f"{eff}\n\n（自动重试：上一次失败原因：{failure[:300]}。"
                f"请换一种方式完成，避免重复同样的错误。）"
            )

    async def _finish_subtask(self, task: Task, st: Subtask, result: AgentResult) -> None:
        state = SubtaskState.succeeded if result.ok else SubtaskState.failed
        task.subtask_state[st.id] = state
        record = {
            "ok": result.ok,
            "output": result.output,
            "artifacts": [str(p) for p in result.artifacts],
            "error": result.error,
        }
        task.subtask_results[st.id] = record
        await self.db.update_subtask(task.id, st.id, state.value, record)
        data: dict = {
            "subtask_id": st.id,
            "title": st.title,
            "agent": st.agent,
            "status": state.value,
        }
        if result.ok:
            data["output"] = result.output[:600]
            data["artifacts"] = record["artifacts"]
        else:
            data["error"] = result.error
        await self._publish("subtask_status", data, task.id)

    # ---------- 辅助 ----------

    # ---- 审核 / 复盘（M3）----

    async def _review(self, task: Task) -> ReviewReport | None:
        try:
            report = await self.assistant.review(self._result_block(task))
        except Exception:
            log.exception("审核生成失败，跳过审核环（直接进入总结）")
            return None
        await self._publish(
            "review_report",
            {
                "verdicts": [v.model_dump() for v in report.verdicts],
                "overall": report.overall,
            },
            task.id,
        )
        return report

    def _review_retry_ids(self, task: Task, report: ReviewReport) -> list[str]:
        """审核不合格、且（a）确实执行成功过（失败/跳过的不在此列）（b）还有重试预算的子任务。"""
        assert task.plan is not None
        ids: list[str] = []
        for v in report.verdicts:
            if v.passed:
                continue
            if task.subtask_state.get(v.subtask_id) is not SubtaskState.succeeded:
                continue
            if task.attempts.get(v.subtask_id, 0) >= self.settings.max_retries:
                continue
            ids.append(v.subtask_id)
        return ids

    async def _requeue(self, task: Task, retry_ids: list[str], report: ReviewReport) -> None:
        assert task.plan is not None
        reasons = {v.subtask_id: v.reason for v in report.verdicts if not v.passed}
        for st in task.plan.subtasks:
            if st.id not in retry_ids:
                continue
            failure = f"审核未通过：{reasons.get(st.id, '质量不达标')}"
            task.attempts[st.id] = task.attempts.get(st.id, 0) + 1  # 重跑消耗一次预算
            task.instructions[st.id] = await self._retry_instruction(task, st, failure)
            task.subtask_state[st.id] = SubtaskState.queued
            task.subtask_results.pop(st.id, None)
            await self.db.update_subtask(task.id, st.id, "queued")
            await self._publish(
                "subtask_status",
                {
                    "subtask_id": st.id,
                    "title": st.title,
                    "agent": st.agent,
                    "status": "retrying",
                    "attempt": task.attempts.get(st.id, 0) + 1,
                    "note": failure,
                },
                task.id,
            )

    async def _retrospect(self, task: Task, summary: str) -> None:
        assert task.plan is not None
        try:
            retro = await self.assistant.retrospect(
                task.plan.goal, self._result_block(task), summary
            )
        except Exception:
            log.exception("复盘失败（不影响任务结果）")
            return
        items = [(m.kind, m.content) for m in retro.memories if m.content.strip()]
        if items:
            await self.db.add_memories(items, task.id)
            await self._publish(
                "retrospect",
                {"memories": [{"kind": k, "content": c} for k, c in items]},
                task.id,
            )
        # 技能候选：发布建议卡片，等用户批准后入库（不阻塞任务收尾）
        for cand in retro.skill_candidates:
            suggestion_id = f"sk-{uuid.uuid4().hex[:8]}"
            self._skill_pending[suggestion_id] = {
                "name": cand.name,
                "description": cand.description,
                "plan_hint": cand.plan_hint,
                "task_id": task.id,
            }
            await self._publish(
                "skill_suggestion",
                {
                    "skill_id": suggestion_id,
                    "name": cand.name,
                    "description": cand.description,
                    "plan_hint": cand.plan_hint,
                },
                task.id,
            )

    def _validate_plan(self, plan: Plan) -> str | None:
        return validate_plan(plan, self.registry)

    def _result_block(self, task: Task) -> str:
        assert task.plan is not None
        lines = [f"任务目标：{task.plan.goal}", "", "各子任务执行结果："]
        for st in task.plan.subtasks:
            state = task.subtask_state.get(st.id)
            r = task.subtask_results.get(st.id, {})
            if r.get("ok"):
                mark = "✅ 成功"
            elif state == SubtaskState.skipped:
                mark = "⏭️ 跳过"
            else:
                mark = "❌ 失败"
            lines.append(f"- [{mark}] {st.id} {st.title}（执行者 {st.agent}）")
            if r.get("ok"):
                lines.append(f"  输出节选：{(r.get('output') or '')[:800]}")
                lines.append(f"  产物：{', '.join(r.get('artifacts') or []) or '无'}")
            elif r.get("error"):
                lines.append(f"  失败原因：{r['error']}")
        return "\n".join(lines)

    def _has_success(self, task: Task) -> bool:
        return any(r.get("ok") for r in task.subtask_results.values())

    async def _fail_task(self, task: Task, reason: str) -> None:
        try:
            task.state = transition(task.state, TaskState.failed)
        except Exception:
            task.state = TaskState.failed
        await self.db.update_task_status(task)
        await self._publish("task_status", {"status": "failed", "reason": reason}, task.id)
        await self._publish("error", {"message": reason, "task_id": task.id})

    async def _publish(self, type_: str, data: dict, task_id: str | None = None) -> None:
        event: dict = {"type": type_, "data": data}
        if task_id:
            event["task_id"] = task_id
        await self.bus.publish(event)

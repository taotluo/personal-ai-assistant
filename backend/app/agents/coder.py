"""编码 Agent：把子任务下发给本机执行器，用 zcode headless 在 workdir 指定的仓库干活。"""

from __future__ import annotations

import logging

from ..config import settings
from ..orchestration.registry import AgentContext, AgentResult
from ..protocol.messages import Subtask
from ..server.executors import ExecutorManager

log = logging.getLogger("paa.coder")


class CoderAgent:
    name = "coder"
    description = (
        "编码：用 zcode 在本机目录执行编程/改码任务（需要本地执行器在线；"
        "必须提供 workdir；默认高危）"
    )
    needs_workdir = True

    def __init__(self, executors: ExecutorManager) -> None:
        self.executors = executors

    async def run(self, subtask: Subtask, ctx: AgentContext) -> AgentResult:
        await ctx.emit({"note": f"已下发到本机执行器，zcode 将在 {subtask.workdir} 工作…"})
        chunk_count = 0

        async def on_chunk(chunk: str) -> None:
            nonlocal chunk_count
            chunk_count += 1
            if chunk_count % 20 == 1:  # 节流：每 20 行报一次尾部进度
                await ctx.emit({"note": chunk[-160:]})

        res = await self.executors.exec(
            kind="zcode",
            prompt=subtask.instruction,
            cwd=subtask.workdir,
            timeout=max(60.0, settings.agent_timeout - 5),
            on_chunk=on_chunk,
        )
        out_dir = settings.artifacts_dir / ctx.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / f"{subtask.id}_zcode.log"
        log_path.write_text(res.get("output") or "（无输出）", encoding="utf-8")
        await ctx.emit({"note": f"zcode 结束，完整日志已存 {log_path}"})
        return AgentResult(
            ok=res["ok"],
            output=(res.get("output") or "")[-4000:],
            artifacts=[log_path],
            error=res.get("error", ""),
        )

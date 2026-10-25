"""本机脚本 Agent：在 workdir 指定目录执行单条 shell 命令（文件操作/一次性脚本）。"""

from __future__ import annotations

import logging

from ..config import settings
from ..orchestration.registry import AgentContext, AgentResult
from ..protocol.messages import Subtask
from ..server.executors import ExecutorManager

log = logging.getLogger("paa.shell")


class ShellAgent:
    name = "shell"
    description = (
        "本机脚本：在 workdir 目录执行一条 shell 命令（instruction 即完整命令内容；"
        "需要本地执行器在线；必须提供 workdir；默认高危）"
    )
    needs_workdir = True

    def __init__(self, executors: ExecutorManager) -> None:
        self.executors = executors

    async def run(self, subtask: Subtask, ctx: AgentContext) -> AgentResult:
        await ctx.emit({"note": f"在本机 {subtask.workdir} 执行命令…"})
        res = await self.executors.exec(
            kind="shell",
            command=subtask.instruction,
            cwd=subtask.workdir,
            timeout=max(60.0, settings.agent_timeout - 5),
        )
        return AgentResult(
            ok=res["ok"],
            output=(res.get("output") or "")[-4000:],
            artifacts=[],
            error=res.get("error", ""),
        )

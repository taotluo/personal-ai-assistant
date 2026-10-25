"""写作 Agent：笔记/文档/邮件草稿/学习总结，产出 markdown 文件。跑在后端。"""

from __future__ import annotations

import logging

from ..config import settings
from ..llm import BaseLLM
from ..orchestration.registry import AgentContext, AgentResult
from ..protocol.messages import Subtask

log = logging.getLogger("paa.writer")

SYSTEM = """你是一名写作者。根据指令完成写作，输出一篇结构清晰、可直接使用的 markdown 文档。
开头用一段话说明本文档是什么、基于什么材料写成。不要空话套话。"""


class WriterAgent:
    name = "writer"
    description = "写作：笔记、文档、邮件草稿、学习总结，产出 markdown 文件"

    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def run(self, subtask: Subtask, ctx: AgentContext) -> AgentResult:
        await ctx.emit({"note": "撰写中…"})
        text = await self.llm.chat(SYSTEM, subtask.instruction, tier="strong")
        out_dir = settings.artifacts_dir / ctx.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{subtask.id}_writing.md"
        path.write_text(text, encoding="utf-8")
        await ctx.emit({"note": f"文档已写入 {path}"})
        return AgentResult(ok=True, output=text[:4000], artifacts=[path])

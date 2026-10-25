"""调研 Agent：联网搜索 + 整理，产出带来源的 markdown 报告。跑在后端。"""

from __future__ import annotations

import logging

from ..config import settings
from ..llm import BaseLLM
from ..orchestration.registry import AgentContext, AgentResult
from ..protocol.messages import Subtask

log = logging.getLogger("paa.research")

SYSTEM = """你是一名调研员。根据指令完成调研，输出一份结构清晰的 markdown 报告：
- 开头给出要点摘要（3-6 条）
- 正文分节展开，关键结论标注来源链接
- 结尾列出"参考来源"

如果联网搜索不可用，基于已有知识回答，并在报告开头注明"⚠️ 未联网，内容基于模型既有知识"。"""


class ResearchAgent:
    name = "research"
    description = "联网调研：搜索+阅读网页，产出带来源的 markdown 报告"

    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def run(self, subtask: Subtask, ctx: AgentContext) -> AgentResult:
        await ctx.emit({"note": "联网搜索与整理中…"})
        text = await self.llm.chat(SYSTEM, subtask.instruction, tier="strong", web_search=True)
        out_dir = settings.artifacts_dir / ctx.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{subtask.id}_research.md"
        path.write_text(text, encoding="utf-8")
        await ctx.emit({"note": f"报告已写入 {path}"})
        return AgentResult(ok=True, output=text[:4000], artifacts=[path])

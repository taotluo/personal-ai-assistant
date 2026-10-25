"""确定性 Mock LLM：无 API key 也能把整条编排链路跑通（冒烟/测试用）。

chat_json 按 schema 类型返回固定结果；生成的方案固定演示
research → writer 依赖链 + s2 高危二次确认链路。
"""

from __future__ import annotations

import logging
import re
from typing import Type, TypeVar

from pydantic import BaseModel

from ..protocol.messages import Subtask
from .client import BaseLLM

log = logging.getLogger("paa.llm.mock")
T = TypeVar("T", bound=BaseModel)

_TASK_WORDS = (
    "调研", "研究", "搜集", "搜索", "整理", "总结",
    "写", "生成", "分析", "对比", "翻译", "做一份", "做一个",
    "执行", "命令", "编码", "代码", "zcode", "shell", "脚本",
)


def _extract(text: str, *markers: str) -> str:
    """从含历史对话的 prompt 块里抽出用户真正的消息。"""
    for m in markers:
        if m in text:
            text = text.split(m, 1)[1]
    return text.strip()


class MockLLMClient(BaseLLM):
    async def chat(
        self, system: str, user: str, *, tier: str = "strong", web_search: bool = False
    ) -> str:
        return f"（mock 回答）关于「{user[:40]}」：本次为模拟执行，编排链路已跑通。"

    async def _messages_chat(self, messages: list[dict], tier: str) -> str:
        return "（mock）ok"

    async def chat_json(
        self, system: str, user: str, schema: Type[T], *, tier: str = "strong"
    ) -> T:
        name = schema.__name__
        if name == "TriageResult":
            msg = _extract(user, "用户最新消息：", "用户需求：")
            is_task = any(w in msg for w in _TASK_WORDS)
            return schema(
                kind="task" if is_task else "chat",
                reply="（mock）这个问题我直接回答：好的，链路正常。",
            )
        if name == "Plan":
            need = _extract(user, "用户需求：").split("相关记忆")[0].strip()
            m = re.search(r"(/\S+)", need)
            path = m.group(1).rstrip("。，,、）)…") if m else "/tmp"
            if any(w in need for w in ("zcode", "编码", "写代码", "代码")):
                return schema(
                    goal=f"（mock）编码任务：{need[:40]}",
                    analysis="mock 编码方案：单子任务下发本机执行器跑 zcode。",
                    subtasks=[Subtask(
                        id="s1", agent="coder", title="zcode 编码",
                        instruction=need, workdir=path, risky=True,
                        expected_output="改动后的文件与说明",
                    )],
                )
            if "shell" in need or "命令" in need:
                return schema(
                    goal=f"（mock）本机脚本：{need[:40]}",
                    analysis="mock shell 方案：单子任务下发本机执行器执行命令。",
                    subtasks=[Subtask(
                        id="s1", agent="shell", title="执行命令",
                        instruction="echo paa-m2-shell-ok > paa-m2-shell.txt",
                        workdir=path, risky=True, expected_output="命令输出",
                    )],
                )
            return schema(
                goal=f"（mock）完成：{need[:40]}",
                analysis="mock 方案：先调研，再基于调研结果成文；s2 标记高危用于演示二次确认。",
                subtasks=[
                    Subtask(
                        id="s1",
                        agent="research",
                        title="联网调研",
                        instruction=f"围绕以下需求做调研并输出带来源的 markdown 报告：{need}",
                        risky=False,
                        expected_output="一份带来源的 markdown 调研报告",
                    ),
                    Subtask(
                        id="s2",
                        agent="writer",
                        title="整理成笔记",
                        instruction="基于 s1 的调研报告整理成一篇结构清晰的笔记。",
                        depends_on=["s1"],
                        risky=True,
                        expected_output="一篇 markdown 笔记",
                    ),
                ],
            )
        if name == "ReviewReport":
            # 从结果块解析 ✅/⏭️/❌ 标记，按实际结果给出 verdict
            verdicts = []
            for m in re.finditer(r"- \[(✅ 成功|⏭️ 跳过|❌ 失败)\] (\S+) ", user):
                mark, sid = m.group(1), m.group(2)
                verdicts.append({
                    "subtask_id": sid,
                    "passed": mark == "✅ 成功",
                    "reason": "mock：达标" if mark == "✅ 成功" else "mock：未达标",
                })
            if not verdicts:
                verdicts = [{"subtask_id": "s1", "passed": True, "reason": "mock"}]
            return schema(verdicts=verdicts, overall="mock 审核完成")
        if name == "RetryHint":
            return schema(instruction=f"（mock 重试指令）{user[:300]}")
        if name == "Retrospect":
            candidates = []
            if "技能" in user or "沉淀" in user or "简报" in user:
                candidates = [{
                    "name": "mock 主题简报",
                    "description": "当用户要针对某主题生成简报时使用",
                    "plan_hint": "research 调研 → writer 成文，两步串行",
                }]
            return schema(
                memories=[{"kind": "lesson", "content": "mock 复盘：链路正常，流程可复用。"}],
                skill_candidates=candidates,
            )
        raise NotImplementedError(f"MockLLMClient 不支持 schema：{name}")

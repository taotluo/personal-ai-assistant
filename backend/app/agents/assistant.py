"""助理 Agent：唯一面向用户的"大脑"——分流、规划、总结。不亲自干活。

提示词即配置：调整助理行为改这里的常量即可，不动编排代码。
"""

from __future__ import annotations

import json
import logging

from ..llm import BaseLLM
from ..protocol.messages import Plan, Retrospect, ReviewReport, RetryHint, TriageResult

log = logging.getLogger("paa.assistant")

TRIAGE_SYSTEM = """你是用户个人AI助理的分流器。判断用户消息属于哪一类，只输出 JSON：
{"kind": "chat" | "task", "reply": "..."}

- chat：闲聊、提问、简单可直接回答的问题（不需要执行多步子任务）。此时 reply 必须给出完整回答。
- task：需要执行一个或多个子任务才能完成的需求（如：调研某主题并写成笔记、写代码、处理文档、整理资料并出报告）。

宁可判为 chat，也不要把简单问题升级成 task；只有确实需要"动手干活"的需求才是 task。
只输出 JSON。"""

PLAN_SYSTEM_TEMPLATE = """你是用户个人AI助理的规划器。把用户需求拆解成可执行的子任务方案。只输出 JSON：
{{
  "goal": "任务目标（一句话）",
  "analysis": "为什么这样拆解（简短，给用户看）",
  "subtasks": [
    {{
      "id": "s1",
      "agent": "执行者名称（必须来自下方目录）",
      "title": "子任务标题",
      "instruction": "给执行者的完整自包含指令——执行者看不到用户对话，必须包含全部上下文、输入来源（如"使用 s1 的输出"）、输出格式与产物要求",
      "depends_on": ["依赖的其他子任务 id，无依赖则空数组"],
      "risky": true 或 false,
      "expected_output": "预期产物与验收标准"
    }}
  ]
}}

可用执行者目录：
{agent_catalog}

规则：
1. 子任务数量不超过 6 个，能一步做完的不要拆碎；没有依赖的子任务应可并行。
2. agent 只能使用目录中存在的名称。
3. depends_on 只能引用本方案内其他子任务的 id，禁止循环依赖。
4. risky 判定：删除文件、git push、对外发送、消费付费 API 等不可逆/有代价的动作 → true。
5. coder/shell 执行者会在用户本机干活：子任务必须带 "workdir": "<本机绝对目录>"（取自用户需求）；用户没有提供目录时，不要安排 coder/shell 子任务，并在 analysis 中说明需要用户补充路径。
6. coder/shell 子任务一律 risky=true（会改动用户本机文件）。
7. 只输出 JSON。"""

SUMMARY_SYSTEM = """你是用户的个人AI助理。所有子任务已执行完毕，请根据执行结果写总结（markdown）：
## 结果总览
（一两句话）
## 各子任务结果
（✅/⏭️/❌ + 一句话说明；❌ 必须给出失败原因）
## 产物清单
（每个产物的路径和说明；无产物可省略本节）
## 建议的下一步
（没有可省略）

语气简洁务实，不客套。用户会直接看到这段话。"""

REVIEW_SYSTEM = """你是用户的个人AI助理的审核员。对照每个子任务的指令与预期产物（expected_output），根据执行结果逐项验收，只输出 JSON：
{"verdicts": [{"subtask_id": "s1", "passed": true 或 false, "reason": "一句话依据"}], "overall": "整体一句话评价"}

判定标准：
- ❌ 失败的子任务 passed=false；⏭️ 跳过的视为未达成 passed=false。
- ✅ 成功的看质量：输出是否达成 expected_output；只有明确不达标才 false，不要苛刻。
只输出 JSON。"""

RETRY_SYSTEM = """你是任务重试规划器。一个子任务失败了或审核未通过。请基于原指令与失败原因，产出一份**换过策略**的自包含重试指令，只输出 JSON：
{"instruction": "完整的重试指令"}

要求：保留原目标；明确改变的做法（避免重复上次失败路径）；仍必须自包含（执行者看不到用户对话）。"""

RETROSPECT_SYSTEM = """你是用户的个人AI助理。任务已结束，请复盘并提炼可供未来任务复用的记忆，只输出 JSON：
{"memories": [{"kind": "lesson" 或 "preference" 或 "fact", "content": "一条具体、可复用的经验/偏好/事实"}],
 "skill_candidates": [{"name": "技能短名", "description": "什么时候用", "plan_hint": "可复用的拆解要点"}]}

kind 说明：lesson=方法教训（下次怎么做更好/避开什么坑）；preference=用户偏好；fact=用户环境或项目的事实。
skill_candidates：仅当本次任务的流程明显可复用（同类需求会反复出现）时提出，最多 1 个，让用户批准后固化为技能；否则给空数组。
规则：只提炼真正可复用的，宁缺毋滥，memories 最多 5 条；不要空话套话。"""


def _history_block(history: list[dict]) -> str:
    if not history:
        return "（无）"
    return "\n".join(f"[{m['role']}] {m['content'][:300]}" for m in history)


def _memories_block(memories: list[dict] | None) -> str:
    if not memories:
        return "（暂无）"
    return "\n".join(f"- [{m['kind']}] {m['content'][:200]}" for m in memories)


def _skills_block(skills: list[dict] | None) -> str:
    if not skills:
        return "（暂无）"
    return "\n".join(
        f"- {s['name']}：{s['description']}｜拆解要点：{s['plan_hint'][:200]}" for s in skills
    )


class AssistantAgent:
    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def triage(self, text: str, history: list[dict]) -> TriageResult:
        user = f"最近对话：\n{_history_block(history)}\n\n用户最新消息：{text}"
        return await self.llm.chat_json(TRIAGE_SYSTEM, user, TriageResult)

    async def make_plan(
        self,
        text: str,
        history: list[dict],
        catalog: dict[str, str],
        memories: list[dict] | None = None,
        skills: list[dict] | None = None,
        unattended: bool = False,
    ) -> Plan:
        system = PLAN_SYSTEM_TEMPLATE.format(
            agent_catalog=json.dumps(catalog, ensure_ascii=False, indent=2)
        )
        user = (
            f"最近对话：\n{_history_block(history)}\n\n"
            f"用户需求：{text}\n\n"
            f"相关记忆（历史任务复盘沉淀，参考但不要机械套用）：\n{_memories_block(memories)}\n\n"
            f"已入库技能（高度匹配时按其 plan_hint 拆解）：\n{_skills_block(skills)}"
        )
        if unattended:
            user += (
                "\n\n（本任务由定时调度自动执行，用户不在场："
                "不要安排高危子任务，也不要安排需要本机执行器的 coder/shell 任务）"
            )
        return await self.llm.chat_json(system, user, Plan)

    async def summarize(self, goal: str, results_block: str) -> str:
        user = f"{results_block}\n\n请基于以上内容输出总结。"
        return await self.llm.chat(SUMMARY_SYSTEM, user)

    async def review(self, results_block: str) -> ReviewReport:
        user = f"{results_block}\n\n请逐项审核并输出 JSON。"
        return await self.llm.chat_json(REVIEW_SYSTEM, user, ReviewReport)

    async def retry_instruction(self, instruction: str, failure: str) -> RetryHint:
        user = (
            f"原指令：{instruction}\n\n"
            f"失败原因/审核意见：{failure}\n\n"
            f"请给出换过策略的重试指令。"
        )
        return await self.llm.chat_json(RETRY_SYSTEM, user, RetryHint)

    async def retrospect(self, goal: str, results_block: str, summary: str) -> Retrospect:
        user = (
            f"任务目标：{goal}\n\n{results_block}\n\n"
            f"给用户的总结：\n{summary}\n\n请复盘并输出 JSON。"
        )
        return await self.llm.chat_json(RETROSPECT_SYSTEM, user, Retrospect)

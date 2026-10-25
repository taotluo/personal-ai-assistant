"""协议对象：助理的结构化输出 + WS 消息信封。

WS 消息的完整契约见 docs/ws-protocol.md（payload 结构以文档为准，
服务端直接构造 dict，不做逐字段强校验，保持前端实现自由度）。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- 助理 Agent 的结构化输出 ----

class TriageResult(BaseModel):
    """分流结果：闲聊直答 or 进任务流。"""
    kind: Literal["chat", "task"]
    reply: str = ""  # kind=chat 时给用户的完整回答


class Subtask(BaseModel):
    id: str = Field(description="子任务短 id，如 s1/s2")
    agent: str = Field(description="执行者名称，必须来自注册表")
    title: str
    instruction: str = Field(description="自包含指令：执行者看不到用户对话")
    depends_on: list[str] = Field(default_factory=list)
    risky: bool = Field(default=False, description="删除/推送/对外发送/花钱 API 等高危动作")
    expected_output: str = Field(description="预期产物与验收标准")
    workdir: str | None = Field(default=None, description="本机工作目录（coder/shell 必填，绝对路径）")


class Plan(BaseModel):
    goal: str
    analysis: str = Field(description="为什么这样拆解（给用户看）")
    subtasks: list[Subtask]


# ---- M3：审核 / 重试 / 复盘 的结构化输出 ----

class ReviewVerdict(BaseModel):
    subtask_id: str
    passed: bool
    reason: str = ""


class ReviewReport(BaseModel):
    verdicts: list[ReviewVerdict]
    overall: str = ""


class RetryHint(BaseModel):
    instruction: str = Field(description="换过策略后的自包含重试指令")


class MemoryItem(BaseModel):
    kind: Literal["lesson", "preference", "fact"]
    content: str


class SkillCandidate(BaseModel):
    name: str = Field(description="技能短名，如「每日学习简报」")
    description: str = Field(description="什么时候用这个技能")
    plan_hint: str = Field(description="可复用的拆解要点（子任务结构/指令要点）")


class Retrospect(BaseModel):
    memories: list[MemoryItem] = Field(default_factory=list)
    skill_candidates: list[SkillCandidate] = Field(
        default_factory=list,
        description="本次流程明显可复用时才提出（≤1 个），用户批准后入库",
    )


# ---- WS 消息信封（服务端 → 前端）----

class Envelope(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    ts: float = Field(default_factory=time.time)

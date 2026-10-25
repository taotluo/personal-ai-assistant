"""执行 Agent 注册表：新增一个执行者 = 实现一个类 + register()，不动编排核心。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

from ..protocol.messages import Subtask


@dataclass
class AgentResult:
    ok: bool
    output: str = ""
    artifacts: list[Path] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentContext:
    task_id: str
    subtask: Subtask
    emit: Callable[[dict], Awaitable[None]]  # 发布进度事件，如 {"note": "搜索中…"}


@runtime_checkable
class ExecutorAgent(Protocol):
    name: str
    description: str

    async def run(self, subtask: Subtask, ctx: AgentContext) -> AgentResult: ...


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, ExecutorAgent] = {}

    def register(self, agent: ExecutorAgent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> ExecutorAgent:
        return self._agents[name]  # KeyError 由编排器兜底

    def names(self) -> list[str]:
        return list(self._agents)

    def catalog(self) -> dict[str, str]:
        """{name: description}，注入助理的规划提示词。"""
        return {a.name: a.description for a in self._agents.values()}

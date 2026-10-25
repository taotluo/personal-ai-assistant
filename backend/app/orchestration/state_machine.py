"""任务状态机。

任务: planning → pending_confirm → running → reviewing → done/failed/cancelled
      （reviewing → running 是 M3 自动重试的回路）
子任务: queued → awaiting_confirm? → running → succeeded/failed/skipped
"""

from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    planning = "planning"
    pending_confirm = "pending_confirm"
    running = "running"
    reviewing = "reviewing"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.planning: {TaskState.pending_confirm, TaskState.failed},
    TaskState.pending_confirm: {TaskState.running, TaskState.cancelled, TaskState.failed},
    TaskState.running: {TaskState.reviewing, TaskState.cancelled, TaskState.failed},
    TaskState.reviewing: {TaskState.done, TaskState.failed, TaskState.running},
    TaskState.done: set(),
    TaskState.failed: set(),
    TaskState.cancelled: set(),
}


class SubtaskState(str, Enum):
    queued = "queued"
    awaiting_confirm = "awaiting_confirm"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class InvalidTransition(RuntimeError):
    pass


def transition(current: TaskState, new: TaskState) -> TaskState:
    """校验并返回新状态；非法迁移抛 InvalidTransition。"""
    if new not in _TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"非法状态迁移: {current.value} -> {new.value}")
    return new

"""进程内事件总线：编排器发布，所有 WS 连接（多端）订阅广播。

事件落库由 persist 回调完成（fire-and-log），保证聊天流可回溯任何任务
的中间过程 —— 这是"自我优化/复盘"的数据地基。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger("paa.bus")


class EventBus:
    def __init__(self, persist: Callable[[dict], Awaitable[None]] | None = None):
        self._subs: set[asyncio.Queue] = set()
        self._persist = persist

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, event: dict) -> None:
        if self._persist is not None:
            try:
                await self._persist(event)
            except Exception:
                log.exception("事件落库失败（不影响广播）")
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("订阅者队列已满，事件被丢弃")

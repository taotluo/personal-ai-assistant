"""本地执行器管理：注册、exec 指令下发、流式回传、超时与断线兜底。

执行器是第二种 WS 客户端（独立脚本 scripts/executor.py 或桌面端内嵌），
通过 executor_hello 注册能力；后端把 exec 指令直发给它（不经事件总线
广播），它流式回传 exec_output / exec_done。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

from fastapi import WebSocket

log = logging.getLogger("paa.executors")

ChunkCallback = Callable[[str], Awaitable[None]]


class ExecutorManager:
    def __init__(self) -> None:
        self._conns: dict[str, WebSocket] = {}
        self._caps: dict[str, list[str]] = {}
        self._futs: dict[str, asyncio.Future] = {}
        self._owner: dict[str, str] = {}  # exec_id -> executor_id
        self._buffers: dict[str, list[str]] = {}
        self._chunk_cbs: dict[str, ChunkCallback] = {}

    # ---- 注册与生命周期 ----

    def register(self, ws: WebSocket, capabilities: list[str]) -> str:
        executor_id = f"ex-{uuid.uuid4().hex[:6]}"
        self._conns[executor_id] = ws
        self._caps[executor_id] = capabilities or []
        log.info(
            "本地执行器上线：%s capabilities=%s（在线 %d 个）",
            executor_id, capabilities, len(self._conns),
        )
        return executor_id

    def unregister(self, executor_id: str) -> None:
        if self._conns.pop(executor_id, None) is None:
            return
        self._caps.pop(executor_id, None)
        log.info("本地执行器离线：%s", executor_id)
        for exec_id in [x for x, o in self._owner.items() if o == executor_id]:
            self._finish(exec_id, ok=False, exit_code=None, error="本地执行器断线")
            self._cleanup(exec_id)

    def online_capabilities(self) -> list[str]:
        caps: set[str] = set()
        for c in self._caps.values():
            caps.update(c)
        return sorted(caps)

    def _pick(self, kind: str) -> str | None:
        for executor_id, caps in self._caps.items():
            if kind in caps:
                return executor_id
        return None

    # ---- exec 下发 ----

    async def exec(
        self,
        *,
        kind: str,
        prompt: str | None = None,
        command: str | None = None,
        cwd: str | None = None,
        timeout: float = 600.0,
        on_chunk: ChunkCallback | None = None,
    ) -> dict:
        """向一个在线执行器下发指令并等结果。不抛异常，失败以 ok=False 返回。

        返回：{ok, exit_code, output(尾部≤200KB), error}
        """
        executor_id = self._pick(kind)
        if executor_id is None:
            return {
                "ok": False, "exit_code": None, "output": "",
                "error": f"没有具备 {kind} 能力的在线执行器（打开桌面端或运行 scripts/executor.py）",
            }
        exec_id = f"e-{uuid.uuid4().hex[:8]}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        # 先注册等待者再发送（竞态保护，与方案确认同款）
        self._futs[exec_id] = fut
        self._owner[exec_id] = executor_id
        self._buffers[exec_id] = []
        self._chunk_cbs[exec_id] = on_chunk
        try:
            await self._conns[executor_id].send_json({
                "type": "exec",
                "payload": {
                    "exec_id": exec_id,
                    "kind": kind,
                    "prompt": prompt,
                    "command": command,
                    "cwd": cwd,
                    "timeout": timeout,
                },
                "ts": time.time(),
            })
        except Exception as err:
            self._cleanup(exec_id)
            return {
                "ok": False, "exit_code": None, "output": "",
                "error": f"下发 exec 指令失败：{err}",
            }
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return {
                "ok": False, "exit_code": None,
                "output": self._tail(exec_id),
                "error": f"执行器超时（>{timeout:.0f}s），后端不再等待；进程可能仍在你的机器上运行",
            }
        finally:
            self._cleanup(exec_id)

    # ---- 执行器上报 ----

    def handle_output(self, exec_id: str, chunk: str) -> None:
        buf = self._buffers.get(exec_id)
        if buf is not None and chunk:
            buf.append(chunk)
        cb = self._chunk_cbs.get(exec_id)
        if cb is not None:
            asyncio.ensure_future(self._safe_cb(cb, chunk))

    def resolve(self, exec_id: str, ok: bool, exit_code: int | None, error: str = "") -> None:
        if exec_id in self._futs:
            self._finish(
                exec_id, ok=ok, exit_code=exit_code,
                error=error or ("" if ok else f"退出码 {exit_code}"),
            )

    # ---- 内部 ----

    async def _safe_cb(self, cb: ChunkCallback, chunk: str) -> None:
        try:
            await cb(chunk)
        except Exception:
            log.exception("on_chunk 回调异常（忽略）")

    def _finish(self, exec_id: str, ok: bool, exit_code: int | None, error: str) -> None:
        fut = self._futs.get(exec_id)
        if fut is not None and not fut.done():
            fut.set_result({
                "ok": ok,
                "exit_code": exit_code,
                "output": self._tail(exec_id),
                "error": error or "",
            })

    def _tail(self, exec_id: str, limit: int = 200_000) -> str:
        buf = self._buffers.get(exec_id)
        if not buf:
            return ""
        return "".join(buf)[-limit:]

    def _cleanup(self, exec_id: str) -> None:
        self._futs.pop(exec_id, None)
        self._owner.pop(exec_id, None)
        self._buffers.pop(exec_id, None)
        self._chunk_cbs.pop(exec_id, None)

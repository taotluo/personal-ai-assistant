"""WebSocket 服务端：token 认证、收消息路由到编排器、事件广播到所有连接。"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..protocol.messages import Plan

log = logging.getLogger("paa.ws")
router = APIRouter()


async def _send(ws: WebSocket, type_: str, payload: dict, task_id: str | None = None) -> None:
    try:
        await ws.send_json(
            {"type": type_, "payload": payload, "task_id": task_id, "ts": time.time()}
        )
    except Exception:
        pass  # 连接已断，广播侧自然清理


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    if websocket.query_params.get("token", "") != settings.token:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    orch = websocket.app.state.orch
    bus = websocket.app.state.bus
    executors = websocket.app.state.executors
    executor_id: str | None = None
    q = bus.subscribe()
    pump = asyncio.create_task(_pump(websocket, q))
    log.info("前端已连接")

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            mtype = raw.get("type")
            if mtype == "user_message" and raw.get("text"):
                # 每条用户消息一个独立协程，不阻塞接收循环
                asyncio.create_task(orch.handle_user_message(str(raw["text"])))
            elif mtype == "plan_decision":
                plan = None
                if raw.get("plan"):
                    try:
                        plan = Plan.model_validate(raw["plan"])
                    except Exception as err:
                        await _send(
                            websocket, "error", {"message": f"修改后的方案格式不合法：{err}"}
                        )
                        continue
                orch.resolve_plan_decision(
                    raw.get("task_id", ""), raw.get("decision", ""), plan
                )
            elif mtype == "confirm_response":
                orch.resolve_confirm(
                    raw.get("confirm_id", ""),
                    bool(raw.get("approved")),
                    str(raw.get("note", "")),
                )
            elif mtype == "skill_decision":
                orch.resolve_skill_decision(str(raw.get("skill_id", "")), bool(raw.get("approved")))
            elif mtype == "executor_hello":
                if executor_id is None:
                    executor_id = executors.register(
                        websocket, [str(c) for c in (raw.get("capabilities") or [])]
                    )
                    pump.cancel()
                    bus.unsubscribe(q)  # 执行器不接收聊天广播，只收直发的 exec 指令
                    await _send(websocket, "executor_ack", {"executor_id": executor_id})
            elif mtype == "exec_output":
                executors.handle_output(str(raw.get("exec_id", "")), str(raw.get("chunk", "")))
            elif mtype == "exec_done":
                executors.resolve(
                    str(raw.get("exec_id", "")),
                    bool(raw.get("ok")),
                    raw.get("exit_code"),
                    str(raw.get("error", "")),
                )
            else:
                await _send(websocket, "error", {"message": f"未知消息类型：{mtype}"})
    except Exception:
        log.exception("WS 连接异常退出")
    finally:
        pump.cancel()
        bus.unsubscribe(q)
        if executor_id is not None:
            executors.unregister(executor_id)
        log.info("前端已断开")


async def _pump(ws: WebSocket, q: asyncio.Queue) -> None:
    while True:
        ev = await q.get()
        await _send(ws, ev.get("type", ""), ev.get("data") or {}, ev.get("task_id"))

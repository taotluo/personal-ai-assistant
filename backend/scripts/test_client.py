#!/usr/bin/env python3
"""最小前端模拟器：连 WS、打印卡片、替你做确认。

一次性模式（冒烟）：
  python scripts/test_client.py --message "帮我调研 X 并写成笔记" --auto-approve
交互模式：
  python scripts/test_client.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from urllib.parse import urlparse

import websockets


def _connect(url: str, token: str):
    """本地地址不走系统代理（macOS 系统代理会劫持 localhost 连接）。"""
    host = (urlparse(url).hostname or "").lower()
    kw = {"max_size": 10 ** 7}
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        kw["proxy"] = None
    return websockets.connect(f"{url}?token={token}", **kw)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="personal-ai-assistant 测试客户端")
    ap.add_argument("--url", default="ws://127.0.0.1:8600/ws")
    ap.add_argument("--token", default="dev-token")
    ap.add_argument("--message", help="一次性模式：发送这条消息，任务到终态后退出")
    ap.add_argument("--auto-approve", action="store_true", help="方案与高危确认自动同意")
    ap.add_argument("--timeout", type=float, default=300, help="一次性模式整体超时（秒）")
    return ap.parse_args()


async def _ask(prompt: str, args: argparse.Namespace) -> bool:
    if args.auto_approve:
        print("（auto-approve）")
        return True
    loop = asyncio.get_running_loop()
    ans = await loop.run_in_executor(None, input, prompt)
    return ans.strip().lower().startswith("y")


async def stdin_reader(ws) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input)
        if line.strip():
            await ws.send(json.dumps({"type": "user_message", "text": line}))


async def handle(ws, env: dict, args: argparse.Namespace, stop: asyncio.Event) -> None:
    t = env.get("type")
    p = env.get("payload") or {}
    tid = env.get("task_id") or "-"

    if t == "message":
        print(f"\n🤖 助理：{p.get('text', '')}")
        if args.message and not env.get("task_id"):
            stop.set()  # 一次性模式：不进任务流的直答，收到即结束
    elif t == "plan_card":
        print(f"\n📋 方案卡片（任务 {tid}）")
        print(f"   目标：{p.get('goal')}")
        print(f"   分析：{p.get('analysis')}")
        for st in p.get("subtasks", []):
            flag = "⚠️高危 " if st.get("risky") else ""
            deps = f"　←　依赖 {','.join(st.get('depends_on') or [])}" if st.get("depends_on") else ""
            print(f"   - [{st['id']}] {flag}{st['title']}（{st['agent']}）{deps}")
            print(f"        指令：{st['instruction'][:80]}…")
        ok = await _ask("批准该方案？(y/n) ", args)
        await ws.send(json.dumps({"type": "plan_decision", "task_id": tid,
                                  "decision": "approve" if ok else "reject"}))
    elif t == "confirm_request":
        print(f"\n⚠️ 高危确认（{p.get('title')}）：{p.get('reason')}")
        ok = await _ask("允许执行？(y/n) ", args)
        await ws.send(json.dumps({"type": "confirm_response", "confirm_id": p["confirm_id"],
                                  "approved": ok}))
    elif t == "subtask_status":
        note = p.get("note") or p.get("error") or ""
        extra = f"：{note}" if note else ""
        print(f"   · [{p.get('subtask_id')}] {p.get('status')}{extra}")
    elif t == "task_status":
        print(f"\n🚦 任务 {tid} → {p.get('status')}{'' if p.get('status') != 'failed' else '：' + str(p.get('reason', ''))}")
        if args.message and p.get("status") in ("done", "failed", "cancelled"):
            stop.set()
    elif t == "summary":
        print(f"\n📄 总结（任务 {tid}）：\n{p.get('text')}")
    elif t == "review_report":
        vs = " ".join(
            f"{'✅' if v.get('passed') else '❌'}{v.get('subtask_id')}"
            + (f"（{v.get('reason')}）" if not v.get("passed") else "")
            for v in p.get("verdicts", [])
        )
        print(f"\n🔍 审核（任务 {tid}）：{vs}")
    elif t == "retrospect":
        mem = "；".join(f"[{m.get('kind')}] {m.get('content')}" for m in p.get("memories", []))
        print(f"\n🧠 记忆沉淀：{mem}")
    elif t == "skill_suggestion":
        print(f"\n🧩 技能建议：{p.get('name')}——{p.get('description')}")
        print(f"   拆解要点：{p.get('plan_hint')}")
        ok = await _ask("采纳入库？(y/n) ", args)
        await ws.send(json.dumps({"type": "skill_decision", "skill_id": p["skill_id"], "approved": ok}))
    elif t == "error":
        print(f"\n❌ 错误：{p.get('message')}")
    sys.stdout.flush()


async def run(args: argparse.Namespace) -> None:
    async with _connect(args.url, args.token) as ws:
        stop = asyncio.Event()

        async def _bail():
            stop.set()
            await ws.close()  # 唤醒接收循环，使其能退出

        if args.message:
            await ws.send(json.dumps({"type": "user_message", "text": args.message}))
            loop = asyncio.get_running_loop()
            loop.call_later(args.timeout, lambda: asyncio.create_task(_bail()))
        elif sys.stdin.isatty():
            print("交互模式：输入消息回车发送，Ctrl-C 退出。")
            asyncio.create_task(stdin_reader(ws))
        else:
            print("未提供 --message 且非交互终端，退出。")
            return
        async for raw in ws:
            await handle(ws, json.loads(raw), args, stop)
            if stop.is_set():
                break


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

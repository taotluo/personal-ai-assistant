#!/usr/bin/env python3
"""本地执行器（独立版）：连后端 WS，注册 zcode/shell 能力，照指令起子进程并流式回传。

桌面端（Tauri）内置了同款执行器；这个脚本用于无桌面端时联调/服务器在远端时
在本地常驻。断线自动重连。

用法：
  python scripts/executor.py                       # 连 ws://127.0.0.1:8600/ws
  python scripts/executor.py --url ws://host:8600/ws --token xxx
  python scripts/executor.py --zcode /path/to/zcode.cjs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
from urllib.parse import urlparse

import websockets

DEFAULT_ZCODE_APP = "/Applications/ZCode.app/Contents/MacOS/ZCode"
DEFAULT_ZCODE = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"

log = logging.getLogger("paa.executor")


def _connect(url: str, token: str):
    """本地地址不走系统代理（macOS 系统代理会劫持 localhost 连接）。"""
    host = (urlparse(url).hostname or "").lower()
    kw = {"max_size": 10 ** 7}
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        kw["proxy"] = None
    return websockets.connect(f"{url}?token={token}", **kw)


class Executor:
    def __init__(self, url: str, token: str, zcode_path: str, zcode_app: str) -> None:
        self.url = url
        self.token = token
        self.zcode_path = zcode_path
        self.zcode_app = zcode_app

    async def run_forever(self) -> None:
        while True:
            try:
                async with _connect(self.url, self.token) as ws:
                    log.info("已连接后端 %s", self.url)
                    await ws.send(json.dumps({
                        "type": "executor_hello",
                        "capabilities": ["zcode", "shell"],
                    }))
                    async for raw in ws:
                        env = json.loads(raw)
                        t = env.get("type")
                        if t == "executor_ack":
                            log.info("注册成功 executor_id=%s",
                                     (env.get("payload") or {}).get("executor_id"))
                        elif t == "exec":
                            asyncio.create_task(self._exec(ws, env.get("payload") or {}))
            except Exception as err:
                log.warning("连接断开：%s，3 秒后重连", err)
            await asyncio.sleep(3)

    def _zcode_cmd(self, prompt: str, cwd: str) -> tuple[list[str], dict | None]:
        """zcode 启动命令与环境。

        关键：优先用 ZCode.app 内置 Electron 以 ELECTRON_RUN_AS_NODE 作为 node
        运行时——系统 node 版本与 App 不一致时，CLI 的 SQLite 迁移会报
        "Migration transaction ownership mismatch" 而拒绝启动。App 不存在（如
        Linux 服务器）才回退系统 node。
        """
        if os.path.exists(self.zcode_app):
            env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
            return [self.zcode_app, self.zcode_path, "-p", prompt, "--cwd", cwd], env
        return ["node", self.zcode_path, "-p", prompt, "--cwd", cwd], None

    async def _exec(self, ws, spec: dict) -> None:
        exec_id = spec.get("exec_id", "")
        kind = spec.get("kind", "")
        cwd = spec.get("cwd") or "."
        timeout = float(spec.get("timeout") or 600)
        spawn_env = None
        if kind == "zcode":
            cmd, spawn_env = self._zcode_cmd(spec.get("prompt") or "", cwd)
        elif kind == "shell":
            cmd = ["/bin/zsh", "-lc", spec.get("command") or spec.get("prompt") or ""]
        else:
            await self._done(ws, exec_id, ok=False, exit_code=None,
                             error=f"不支持的能力：{kind}")
            return
        log.info("exec %s [%s] cwd=%s", exec_id, shlex.join(cmd)[:160], cwd)
        try:
            os.makedirs(cwd, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=spawn_env,
            )
        except Exception as err:
            await self._done(ws, exec_id, ok=False, exit_code=None,
                             error=f"启动失败：{err}")
            return

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip("\n")
                if text:
                    await ws.send(json.dumps({
                        "type": "exec_output", "exec_id": exec_id, "chunk": text + "\n",
                    }))

        try:
            await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout=timeout)
            await self._done(ws, exec_id, ok=(proc.returncode == 0),
                             exit_code=proc.returncode)
        except asyncio.TimeoutError:
            proc.kill()
            await self._done(ws, exec_id, ok=False, exit_code=None,
                             error=f"执行超时（>{timeout:.0f}s），进程已终止")

    async def _done(self, ws, exec_id: str, *, ok: bool, exit_code, error: str = "") -> None:
        try:
            await ws.send(json.dumps({
                "type": "exec_done", "exec_id": exec_id, "ok": ok,
                "exit_code": exit_code, "error": error,
            }))
            log.info("exec %s 完成 ok=%s exit=%s", exec_id, ok, exit_code)
        except Exception:
            log.warning("exec %s 结果上报失败（连接可能已断）", exec_id)


def main() -> None:
    ap = argparse.ArgumentParser(description="personal-ai-assistant 本地执行器")
    ap.add_argument("--url", default="ws://127.0.0.1:8600/ws")
    ap.add_argument("--token", default="dev-token")
    ap.add_argument("--zcode", default=DEFAULT_ZCODE, help="zcode.cjs 路径")
    ap.add_argument("--zcode-app", default=DEFAULT_ZCODE_APP,
                    help="ZCode.app 可执行文件路径（用其内置 node 运行时跑 CLI）")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not os.path.exists(args.zcode):
        log.warning("zcode 未找到：%s（coder 任务会启动失败，可用 --zcode 指定路径）", args.zcode)
    if os.name != "nt" and not os.path.exists(args.zcode_app):
        log.warning("ZCode.app 未找到：%s（将回退系统 node，版本不匹配时 zcode 可能启动失败）",
                    args.zcode_app)
    asyncio.run(Executor(args.url, args.token, args.zcode, args.zcode_app).run_forever())


if __name__ == "__main__":
    main()

"""M2：执行器管理 / coder Agent / 方案校验（workdir）。"""

import asyncio

from app.agents.coder import CoderAgent
from app.orchestration.orchestrator import validate_plan
from app.orchestration.registry import AgentContext, AgentRegistry
from app.protocol.messages import Plan, Subtask
from app.server.executors import ExecutorManager


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _coder_plan(workdir) -> Plan:
    return Plan(
        goal="g", analysis="a",
        subtasks=[Subtask(id="s1", agent="coder", title="t", instruction="i",
                          risky=True, expected_output="o", workdir=workdir)],
    )


async def test_validate_requires_workdir():
    reg = AgentRegistry()
    reg.register(CoderAgent(ExecutorManager()))
    err = validate_plan(_coder_plan(None), reg)
    assert err and "workdir" in err
    assert validate_plan(_coder_plan("/tmp"), reg) is None


async def test_exec_without_executor():
    m = ExecutorManager()
    res = await m.exec(kind="zcode", prompt="x", cwd="/tmp", timeout=1)
    assert not res["ok"] and "执行器" in res["error"]


async def test_exec_roundtrip_and_disconnect():
    m = ExecutorManager()
    ws = FakeWs()
    eid = m.register(ws, ["zcode", "shell"])

    task = asyncio.create_task(m.exec(kind="zcode", prompt="do", cwd="/tmp", timeout=2))
    await asyncio.sleep(0.05)
    payload = ws.sent[0]["payload"]
    assert payload["kind"] == "zcode" and payload["prompt"] == "do"
    m.handle_output(payload["exec_id"], "line1\n")
    m.resolve(payload["exec_id"], ok=True, exit_code=0)
    res = await task
    assert res["ok"] and "line1" in res["output"]

    # 断线兜底：pending 的 exec 以失败收场
    task2 = asyncio.create_task(m.exec(kind="shell", command="echo hi", cwd="/tmp", timeout=2))
    await asyncio.sleep(0.05)
    m.unregister(eid)
    res2 = await task2
    assert not res2["ok"] and "断线" in res2["error"]


async def test_exec_timeout():
    m = ExecutorManager()
    m.register(FakeWs(), ["zcode"])
    res = await m.exec(kind="zcode", prompt="x", cwd="/tmp", timeout=0.2)
    assert not res["ok"] and "超时" in res["error"]


async def test_coder_agent_success(tmp_path):
    m = ExecutorManager()
    ws = FakeWs()
    m.register(ws, ["zcode"])
    agent = CoderAgent(m)
    st = Subtask(id="s1", agent="coder", title="t", instruction="do it",
                 risky=True, expected_output="o", workdir="/tmp")
    notes = []

    async def emit(data):
        notes.append(data)

    ctx = AgentContext(task_id="t-m2test", subtask=st, emit=emit)
    task = asyncio.create_task(agent.run(st, ctx))
    for _ in range(100):
        if ws.sent:
            break
        await asyncio.sleep(0.02)
    assert ws.sent, "coder 未下发 exec"
    m.handle_output(ws.sent[0]["payload"]["exec_id"], "working\n")
    m.resolve(ws.sent[0]["payload"]["exec_id"], ok=True, exit_code=0)
    res = await asyncio.wait_for(task, timeout=3)
    assert res.ok
    assert res.artifacts and res.artifacts[0].exists()  # zcode 日志产物
    assert any("已下发" in n.get("note", "") for n in notes)

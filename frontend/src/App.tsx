import { useEffect, useRef, useState } from "react";
import { PaaSocket, isTauri, type Status } from "./ws";
import type {
  ConfirmRequest, Envelope, Plan, SubtaskStatus, Summary, TaskDetail, HistoryTask,
} from "./types";

// ---------- 聊天流条目模型 ----------

type Item =
  | { kind: "text"; own: boolean; text: string }
  | { kind: "status"; text: string; level: "info" | "ok" | "bad" }
  | { kind: "plan"; key: string; task: string; plan: Plan; decided?: "approve" | "reject" }
  | { kind: "confirm"; key: string; task: string; req: ConfirmRequest; answered?: boolean }
  | { kind: "log"; key: string; task: string; lines: string[] }
  | { kind: "summary"; key: string; task: string; summary: Summary }
  | {
      kind: "skill";
      key: string;
      skill_id: string;
      name: string;
      description: string;
      plan_hint: string;
      answered?: boolean;
    };

const DEFAULT_URL = "ws://127.0.0.1:8600/ws";
const DEFAULT_TOKEN = "dev-token";

export default function App() {
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [chatStatus, setChatStatus] = useState<Status>("idle");
  const [execStatus, setExecStatus] = useState<Status>("idle");
  const [url, setUrl] = useState(() => localStorage.getItem("paa.url") || DEFAULT_URL);
  const [token, setToken] = useState(() => localStorage.getItem("paa.token") || DEFAULT_TOKEN);
  const [histOpen, setHistOpen] = useState(false);
  const [histTasks, setHistTasks] = useState<HistoryTask[]>([]);
  const [histDetail, setHistDetail] = useState<TaskDetail | null>(null);

  const apiBase = url.replace(/^ws/, "http").replace(/\/ws\/?$/, "");

  const chatRef = useRef<PaaSocket | null>(null);
  const execRef = useRef<PaaSocket | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const push = (item: Item) => setItems((prev) => [...prev, item]);
  const patch = (key: string, fn: (it: Item) => Item) =>
    setItems((prev) => prev.map((it) => (("key" in it && it.key === key) ? fn(it) : it)));

  // ---------- 聊天连接 ----------

  const connectAll = () => {
    localStorage.setItem("paa.url", url);
    localStorage.setItem("paa.token", token);

    const chat = new PaaSocket({ onEvent: handleEvent, onStatus: setChatStatus });
    chatRef.current = chat;
    chat.connect(url, token);

    // 内嵌本地执行器：桌面端才可用（浏览器里 invoke 不可用）
    if (isTauri()) {
      const ex = new PaaSocket({
        onEvent: handleExecEvent,
        onStatus: setExecStatus,
        onOpen: () =>
          execRef.current?.send({ type: "executor_hello", capabilities: ["zcode", "shell"] }),
      });
      execRef.current = ex;
      ex.connect(url, token);
    }
  };

  useEffect(() => {
    connectAll();
    return () => {
      chatRef.current?.close();
      execRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [items]);

  // ---------- 聊天事件 ----------

  const handleEvent = (env: Envelope) => {
    const p = env.payload || {};
    switch (env.type) {
      case "message":
        if (env.task_id) break; // 任务内的提示消息不重复展示（卡片/日志已覆盖）
        push({ kind: "text", own: false, text: p.text || "" });
        break;
      case "plan_card":
        push({ kind: "plan", key: `plan-${env.ts}`, task: env.task_id!, plan: p });
        break;
      case "confirm_request":
        push({ kind: "confirm", key: p.confirm_id, task: env.task_id!, req: p });
        break;
      case "subtask_status": {
        const s = p as SubtaskStatus;
        const line =
          s.status === "progress"
            ? `· ${s.note || ""}`
            : s.status === "retrying"
              ? `🔁 [${s.subtask_id}] 第 ${s.attempt ?? "?"} 次重试：${s.note || ""}`
              : s.status === "succeeded"
              ? `✅ [${s.subtask_id}] ${s.title || ""} 完成${s.artifacts?.length ? `　产物：${s.artifacts.join("，")}` : ""}`
              : s.status === "failed"
                ? `❌ [${s.subtask_id}] ${s.title || ""} 失败：${s.error || ""}`
                : s.status === "skipped"
                  ? `⏭️ [${s.subtask_id}] ${s.title || ""} 跳过：${s.note || ""}`
                  : `▶️ [${s.subtask_id}] ${s.title || ""} 开始（${s.agent || ""}）`;
        setItems((prev) => {
          const last = [...prev].reverse().find((it) => it.kind === "log" && it.task === env.task_id);
          if (last) {
            return prev.map((it) =>
              it === last && it.kind === "log" ? { ...it, lines: [...it.lines, line] } : it
            );
          }
          return [...prev, { kind: "log", key: `log-${env.task_id}-${env.ts}`, task: env.task_id!, lines: [line] }];
        });
        break;
      }
      case "task_status": {
        const map: Record<string, "info" | "ok" | "bad"> = {
          planning: "info", pending_confirm: "info", running: "info",
          reviewing: "info", done: "ok", failed: "bad", cancelled: "info",
        };
        push({
          kind: "status",
          text: `任务 ${env.task_id} → ${p.status}${p.reason ? `：${p.reason}` : ""}`,
          level: map[p.status] || "info",
        });
        break;
      }
      case "summary":
        push({ kind: "summary", key: `sum-${env.ts}`, task: env.task_id!, summary: p });
        break;
      case "review_report": {
        const vs = (p.verdicts || []) as { subtask_id: string; passed: boolean; reason?: string }[];
        const line = vs
          .map((v) => `${v.passed ? "✅" : "❌"}${v.subtask_id}${v.passed ? "" : `（${v.reason || ""}）`}`)
          .join(" ");
        push({ kind: "status", text: `🔍 审核：${line}`, level: "info" });
        break;
      }
      case "retrospect": {
        const mem = (p.memories || []) as { kind: string; content: string }[];
        push({
          kind: "status",
          text: `🧠 记忆沉淀：${mem.map((m) => `[${m.kind}] ${m.content}`).join("；")}`,
          level: "info",
        });
        break;
      }
      case "skill_suggestion":
        push({
          kind: "skill",
          key: p.skill_id,
          skill_id: p.skill_id,
          name: p.name || "",
          description: p.description || "",
          plan_hint: p.plan_hint || "",
        });
        break;
      case "error":
        push({ kind: "status", text: `错误：${p.message || ""}`, level: "bad" });
        break;
    }
  };

  // ---------- 内嵌执行器 ----------

  // PaaSocket 打开后注册执行器（补一个 onopen 钩子的最小实现）
  const handleExecEvent = (env: Envelope) => {
    if (env.type !== "exec") return;
    void runExec(env.payload);
  };

  const runExec = async (spec: {
    exec_id: string; kind: string; prompt?: string; command?: string; cwd?: string; timeout?: number;
  }) => {
    const reply = (obj: unknown) => execRef.current?.send(obj);
    if (!isTauri()) {
      reply({ type: "exec_done", exec_id: spec.exec_id, ok: false, exit_code: null, error: "浏览器模式不支持本机执行" });
      return;
    }
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const { listen } = await import("@tauri-apps/api/event");
      let unOut: (() => void) | null = null;
      let unDone: (() => void) | null = null;
      unOut = await listen<string>(`exec-out:${spec.exec_id}`, (e) => {
        reply({ type: "exec_output", exec_id: spec.exec_id, chunk: `${e.payload}\n` });
      });
      unDone = await listen<any>(`exec-done:${spec.exec_id}`, async (e) => {
        unOut?.(); unDone?.();
        reply({
          type: "exec_done", exec_id: spec.exec_id,
          ok: !!e.payload?.ok, exit_code: e.payload?.exit_code ?? null, error: e.payload?.error || "",
        });
      });
      await invoke("spawn_exec", {
        execId: spec.exec_id, kind: spec.kind,
        prompt: spec.prompt || "", command: spec.command || "",
        cwd: spec.cwd || ".", timeoutSecs: Math.floor(spec.timeout || 600),
      });
    } catch (err) {
      reply({ type: "exec_done", exec_id: spec.exec_id, ok: false, exit_code: null, error: String(err) });
    }
  };

  // ---------- 动作 ----------

  const send = () => {
    const text = input.trim();
    if (!text || chatStatus !== "open") return;
    chatRef.current?.send({ type: "user_message", text });
    push({ kind: "text", own: true, text });
    setInput("");
  };

  const decidePlan = (item: Extract<Item, { kind: "plan" }>, decision: "approve" | "reject") => {
    chatRef.current?.send({ type: "plan_decision", task_id: item.task, decision });
    patch(item.key, (it) => (it.kind === "plan" ? { ...it, decided: decision } : it));
  };

  const answerConfirm = (item: Extract<Item, { kind: "confirm" }>, approved: boolean) => {
    chatRef.current?.send({ type: "confirm_response", confirm_id: item.req.confirm_id, approved });
    patch(item.key, (it) => (it.kind === "confirm" ? { ...it, answered: true } : it));
  };

  const decideSkill = (item: Extract<Item, { kind: "skill" }>, approved: boolean) => {
    chatRef.current?.send({ type: "skill_decision", skill_id: item.skill_id, approved });
    patch(item.key, (it) => (it.kind === "skill" ? { ...it, answered: true } : it));
  };

  const openHistory = async () => {
    setHistOpen(true);
    setHistDetail(null);
    try {
      const res = await fetch(`${apiBase}/api/tasks`);
      setHistTasks(await res.json());
    } catch {
      setHistTasks([]);
    }
  };

  const openTaskDetail = async (id: string) => {
    try {
      const res = await fetch(`${apiBase}/api/tasks/${id}`);
      setHistDetail(res.ok ? await res.json() : null);
    } catch {
      setHistDetail(null);
    }
  };

  // ---------- 渲染 ----------

  return (
    <div className="app">
      <header className="bar">
        <span className="brand">个人 AI 助理</span>
        <span className={`dot ${chatStatus}`} title={chatStatus} />
        <input className="conn" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} />
        <input className="conn token" value={token} onChange={(e) => setToken(e.target.value)} spellCheck={false} />
        <button onClick={connectAll}>连接</button>
        <button onClick={openHistory}>历史</button>
        <span className="exec-badge" title="内嵌本地执行器（zcode/shell）">
          执行器 {isTauri() ? <span className={`dot inline ${execStatus}`} /> : "（浏览器不可用）"}
        </span>
      </header>

      <div className="list" ref={listRef}>
        {items.map((it, i) => {
          if (it.kind === "text")
            return (
              <div key={i} className={`bubble ${it.own ? "own" : ""}`}>{it.text}</div>
            );
          if (it.kind === "status")
            return <div key={i} className={`status ${it.level}`}>{it.text}</div>;
          if (it.kind === "plan")
            return (
              <div key={it.key} className="card plan">
                <div className="card-title">📋 方案 · {it.plan.goal}</div>
                <div className="muted">{it.plan.analysis}</div>
                {it.plan.subtasks.map((st) => (
                  <div key={st.id} className="subtask">
                    <div>
                      <b>[{st.id}] {st.title}</b>{" "}
                      <span className="tag">{st.agent}</span>
                      {st.risky && <span className="tag risk">⚠️ 高危</span>}
                      {st.workdir && <span className="tag dir">{st.workdir}</span>}
                      {st.depends_on.length > 0 && <span className="tag">← {st.depends_on.join(",")}</span>}
                    </div>
                    <div className="muted small">{st.instruction}</div>
                  </div>
                ))}
                {it.decided ? (
                  <div className="muted">{it.decided === "approve" ? "✅ 已批准" : "🚫 已否决"}</div>
                ) : (
                  <div className="row">
                    <button className="primary" onClick={() => decidePlan(it, "approve")}>批准执行</button>
                    <button onClick={() => decidePlan(it, "reject")}>否决</button>
                  </div>
                )}
              </div>
            );
          if (it.kind === "confirm")
            return (
              <div key={it.key} className="card confirm">
                <div className="card-title">⚠️ 高危确认 · {it.req.title}</div>
                <div>{it.req.reason}</div>
                {it.answered ? (
                  <div className="muted">已答复</div>
                ) : (
                  <div className="row">
                    <button className="primary" onClick={() => answerConfirm(it, true)}>允许执行</button>
                    <button onClick={() => answerConfirm(it, false)}>拒绝</button>
                  </div>
                )}
              </div>
            );
          if (it.kind === "log")
            return (
              <div key={it.key} className="card log">
                {it.lines.map((l, j) => <div key={j}>{l}</div>)}
              </div>
            );
          if (it.kind === "skill")
            return (
              <div key={it.key} className="card skill">
                <div className="card-title">🧩 技能建议 · {it.name}</div>
                <div>{it.description}</div>
                <div className="muted small">拆解要点：{it.plan_hint}</div>
                {it.answered ? (
                  <div className="muted">已答复</div>
                ) : (
                  <div className="row">
                    <button className="primary" onClick={() => decideSkill(it, true)}>采纳入库</button>
                    <button onClick={() => decideSkill(it, false)}>忽略</button>
                  </div>
                )}
              </div>
            );
          return (
            <div key={it.key} className="card summary">
              <div className="card-title">📄 总结</div>
              <div className="pre">{it.summary.text}</div>
            </div>
          );
        })}
      </div>

      <footer className="composer">
        <textarea
          value={input}
          placeholder={chatStatus === "open" ? "输入消息，Enter 发送（Shift+Enter 换行）" : "未连接后端…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
          rows={3}
        />
        <button className="primary send" onClick={send} disabled={chatStatus !== "open"}>发送</button>
      </footer>

      {histOpen && (
        <div className="drawer-mask" onClick={() => setHistOpen(false)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-head">
              <b>历史任务</b>
              <button onClick={() => setHistOpen(false)}>关闭</button>
            </div>
            <div className="drawer-list">
              {histTasks.map((t) => (
                <div key={t.id} className="hist-item" onClick={() => openTaskDetail(t.id)}>
                  <span className={`tag st-${t.status}`}>{t.status}</span> {t.title}
                </div>
              ))}
              {!histTasks.length && <div className="muted">（暂无任务）</div>}
            </div>
            {histDetail && (
              <div className="drawer-detail">
                <div className="card-title">{histDetail.title}</div>
                <div className="muted small">状态：{histDetail.status}</div>
                {histDetail.plan && (
                  <div className="small">
                    目标：{histDetail.plan.goal}
                    {histDetail.plan.subtasks.map((s) => (
                      <div key={s.id}>
                        · [{s.id}] {s.title}（{s.agent}）→ {histDetail.subtasks.find((x) => x.id === s.id)?.status}
                        {(histDetail.subtasks.find((x) => x.id === s.id)?.result?.artifacts || []).map((a: string, i: number) => (
                          <div key={i} className="muted small">产物：{a}</div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
                {histDetail.messages
                  .filter((m) => m.role === "assistant")
                  .slice(-1)
                  .map((m, i) => (
                    <div key={i} className="pre small">{"\n" + m.content}</div>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// WS 协议类型（契约见 docs/ws-protocol.md）

export interface Envelope<T = any> {
  type: string;
  payload: T;
  task_id?: string | null;
  ts: number;
}

export interface Subtask {
  id: string;
  agent: string;
  title: string;
  instruction: string;
  depends_on: string[];
  risky: boolean;
  expected_output: string;
  workdir?: string | null;
}

export interface Plan {
  goal: string;
  analysis: string;
  subtasks: Subtask[];
}

export interface SubtaskStatus {
  subtask_id: string;
  title?: string;
  agent?: string;
  status: "running" | "progress" | "retrying" | "succeeded" | "failed" | "skipped";
  note?: string;
  attempt?: number;
  output?: string;
  artifacts?: string[];
  error?: string;
}

export interface ConfirmRequest {
  confirm_id: string;
  subtask_id: string;
  title: string;
  reason: string;
}

export interface Summary {
  text: string;
  subtasks: { id: string; title: string; ok: boolean; artifacts: string[] }[];
}

// ---- 历史回溯（REST）----

export interface HistoryTask {
  id: string;
  title: string;
  status: string;
  created_at: number;
}

export interface TaskDetail {
  id: string;
  title: string;
  status: string;
  plan: Plan | null;
  created_at: number;
  updated_at: number;
  subtasks: { id: string; status: string; result: any }[];
  messages: { role: string; content: string }[];
}

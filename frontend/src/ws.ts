import type { Envelope } from "./types";

export type Status = "idle" | "connecting" | "open" | "closed" | "error";

export interface PaaSocketOpts {
  onEvent: (env: Envelope) => void;
  onStatus: (s: Status) => void;
  onOpen?: () => void;
}

export class PaaSocket {
  private ws: WebSocket | null = null;

  constructor(private opts: PaaSocketOpts) {}

  connect(url: string, token: string) {
    this.close();
    this.opts.onStatus("connecting");
    const ws = new WebSocket(`${url}?token=${encodeURIComponent(token)}`);
    ws.onopen = () => {
      this.opts.onStatus("open");
      this.opts.onOpen?.();
    };
    ws.onclose = () => this.opts.onStatus("closed");
    ws.onerror = () => this.opts.onStatus("error");
    ws.onmessage = (e) => {
      try {
        this.opts.onEvent(JSON.parse(e.data));
      } catch {
        // 非 JSON 帧忽略
      }
    };
    this.ws = ws;
  }

  send(obj: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  close() {
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }
}

export const isTauri = (): boolean => "__TAURI_INTERNALS__" in window;

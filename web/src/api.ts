// API 封装：token 管理、fetch 调用、SSE 流解析（共识 #10：localStorage JWT）

const TOKEN_KEY = "da_token";

export type ResultEvent = {
  type: "result";
  narration: string;
  columns: string[];
  rows: unknown[][];
  total_rows: number | null;
  attempts: number;
};
export type ClarificationEvent = { type: "clarification"; question: string };
export type ProgressEvent = { type: "progress"; stage: string };
export type ErrorEvent = { type: "error"; detail: string };
export type ChatEvent = ResultEvent | ClarificationEvent | ProgressEvent | ErrorEvent;

export type HistoryRecord = {
  question: string;
  narration: string;
  columns: string[];
  rows: unknown[][];
  total_rows: number | null;
  status: string;
};

let token = localStorage.getItem(TOKEN_KEY) || "";

export function setToken(t: string) {
  token = t;
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  token = "";
  localStorage.removeItem(TOKEN_KEY);
}
export function hasToken() {
  return !!token;
}

async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("da:unauthorized"));
    throw new Error("未认证");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

export async function* chatStream(
  sessionId: string,
  body: { question: string; resume?: string },
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("da:unauthorized"));
    throw new Error("未认证");
  }
  if (!res.ok) throw new Error(`请求失败 (${res.status})`);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6));
    }
  }
}

export const apiRegister = (username: string, password: string) =>
  api<{ token: string; user_id: string }>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });

export const apiLogin = (username: string, password: string) =>
  api<{ token: string; user_id: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });

export const apiCreateSession = () =>
  api<{ session_id: string }>("/sessions", { method: "POST" });

export const apiListSessions = () => api<{ sessions: string[] }>("/sessions");

export const apiHistory = (sessionId: string) =>
  api<{ history: HistoryRecord[] }>(`/sessions/${sessionId}/history`);

export async function uploadFile(sessionId: string, file: File): Promise<void> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/sessions/${sessionId}/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("da:unauthorized"));
    throw new Error("未认证");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `上传失败 (${res.status})`);
  }
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

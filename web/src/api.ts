// API 封装：fetch 调用、SSE 流解析（本地单用户模式，无认证）

export type ChartSpec = {
  type: "bar" | "line" | "pie" | "scatter";
  title: string;
  x: string[];
  series: { name: string; data: (number | null)[] }[];
};

export type ResultEvent = {
  type: "result";
  narration: string;
  columns: string[];
  rows: unknown[][];
  total_rows: number | null;
  attempts: number;
  chart?: ChartSpec | null;
};
export type ClarificationEvent = { type: "clarification"; question: string };
export type TraceDetail = { node: string; input: unknown; output: unknown };
export type ProgressEvent = {
  type: "progress";
  stage: string;
  detail?: TraceDetail | null;
  skipped?: boolean;
};
export type ToolEvent = {
  type: "tool";
  node: string;
  detail: { tool: string; kind: "call" | "result"; args?: unknown; content?: unknown };
};
export type ErrorEvent = { type: "error"; detail: string };
export type ChatEvent =
  | ResultEvent
  | ClarificationEvent
  | ProgressEvent
  | ToolEvent
  | ErrorEvent;

export type HistoryRecord = {
  question: string;
  narration: string;
  columns: string[];
  rows: unknown[][];
  total_rows: number | null;
  status: string;
  chart?: ChartSpec | null;
  trace?: {
    stage: string;
    detail: TraceDetail;
    skipped?: boolean;
    tools?: { tool: string; args?: unknown; content?: unknown }[];
  }[] | null;
};

async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

export type SuggestionsResponse = {
  suggestions: string[];
  kind: "rule" | "llm" | "generic";
  offset: number;
  pool_size: number;
  has_files: boolean;
};

export const apiSuggestions = (sessionId: string, offset: number) =>
  api<SuggestionsResponse>(
    `/sessions/${sessionId}/suggestions?offset=${offset}`,
  );

export const apiGetSettings = () => api<Record<string, string>>("/settings");

export const apiSaveSettings = (values: Record<string, string>) =>
  api<{ ok: boolean; saved: string[] }>("/settings", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });

export async function* chatStream(
  sessionId: string,
  body: { question: string; resume?: string },
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`/sessions/${sessionId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
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

export const apiCreateSession = () =>
  api<{ session_id: string }>("/sessions", { method: "POST" });

export type SessionInfo = { session_id: string; title: string };

export const apiListSessions = () => api<{ sessions: SessionInfo[] }>("/sessions");

export const apiHistory = (sessionId: string) =>
  api<{ history: HistoryRecord[] }>(`/sessions/${sessionId}/history`);

export type UploadedFile = { filename: string; path: string };

export const apiListFiles = (sessionId: string) =>
  api<{ files: UploadedFile[] }>(`/sessions/${sessionId}/files`);

export const apiDeleteFile = (sessionId: string, path: string) =>
  api<{ ok: boolean }>(
    `/sessions/${sessionId}/files?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );

export async function uploadFile(sessionId: string, file: File): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`/sessions/${sessionId}/upload`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `上传失败 (${res.status})`);
  }
  const body = (await res.json()) as { path: string };
  return body.path;
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

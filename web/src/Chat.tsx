import { useEffect, useRef, useState } from "react";
import {
  apiCreateSession,
  apiDeleteFile,
  apiHistory,
  apiListFiles,
  apiListSessions,
  chatStream,
  HistoryRecord,
  ResultEvent,
  SessionInfo,
  toCsv,
  TraceDetail,
  UploadedFile,
  uploadFile,
} from "./api";
import ChartBox from "./ChartBox";
import Landing from "./Landing";
import Logo from "./Logo";

type Message =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string } // clarify 提问
  | { kind: "result"; data: ResultEvent; trace?: TraceEntry[] };

type ToolPair = { tool: string; args?: unknown; content?: unknown };
type TraceEntry = { stage: string; detail: TraceDetail; skipped?: boolean; tools?: ToolPair[] };

const MAX_TABLE_ROWS = 50;

export default function Chat() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [input, setInput] = useState("");
  const [pendingClarify, setPendingClarify] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  // 从首页隐式创建进入：触发输入卡下落动画
  const [fromLanding, setFromLanding] = useState(false);
  // 会话历史抽屉（首页与聊天视图共用）
  const [historyOpen, setHistoryOpen] = useState(false);
  // 实时执行轨迹（thinking 期间渲染；result 后转入结果气泡折叠区）
  const [liveTrace, setLiveTrace] = useState<TraceEntry[]>([]);
  const liveTraceRef = useRef<TraceEntry[]>([]);
  const pendingToolsRef = useRef<ToolPair[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  // 会话切换竞态防护：序号守卫 + 中止进行中的 SSE 流
  const sendSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // 切换会话/回首页：使进行中的流失效（迟到事件不再写入新视图）
  const invalidateStream = () => {
    sendSeq.current++;
    abortRef.current?.abort();
    abortRef.current = null;
    setThinking(false);
    setStage(null);
    setLiveTrace([]);
    liveTraceRef.current = [];
    pendingToolsRef.current = [];
  };

  useEffect(() => {
    apiListSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);

  useEffect(() => {
    const behavior: ScrollBehavior = window.matchMedia("(prefers-reduced-motion: reduce)")
      .matches
      ? "auto"
      : "smooth";
    bottomRef.current?.scrollIntoView({ behavior });
  }, [messages, thinking]);

  // textarea 随内容自适应高度（上限 max-h-72）
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 288) + "px";
  }, [input]);

  const openSession = async (sid: string) => {
    invalidateStream();
    setCurrent(sid);
    setPendingClarify(null);
    setError("");
    setFromLanding(false);
    setFiles([]);
    setMessages([]);
    try {
      const { history } = await apiHistory(sid);
      setMessages(
        history.flatMap((rec: HistoryRecord) => [
          { kind: "user", text: rec.question } as Message,
          {
            kind: "result",
            data: {
              type: "result",
              narration: rec.narration,
              columns: rec.columns,
              rows: rec.rows,
              total_rows: rec.total_rows,
              attempts: 0,
              chart: rec.chart ?? null,
            },
            trace: rec.trace && rec.trace.length ? (rec.trace as TraceEntry[]) : undefined,
          } as Message,
        ]),
      );
    } catch {
      setMessages([]);
    }
    apiListFiles(sid)
      .then((r) => setFiles(r.files))
      .catch(() => {});
  };

  // 显式新建：回到首页，首条分析目标发送时才隐式创建会话
  const newSession = () => {
    invalidateStream();
    setCurrent(null);
    setMessages([]);
    setFiles([]);
    setPendingClarify(null);
    setError("");
    setInput("");
    setStage(null);
    setFromLanding(false);
  };

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!current) {
      setError("请先创建或选择一个会话，再上传文件");
      return;
    }
    const list = Array.from(e.target.files || []);
    e.target.value = "";
    for (const f of list) {
      try {
        const path = await uploadFile(current, f);
        setFiles((fs) => [...fs, { filename: f.name, path }]);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  };

  // 首页首条分析目标：隐式创建会话 → 进入聊天视图 → 上传暂存文件并开始分析
  const startFromLanding = async (text: string, files: File[]) => {
    setStarting(true);
    setError("");
    try {
      const { session_id } = await apiCreateSession();
      setSessions((s) => [{ session_id, title: text.slice(0, 24) }, ...s]);
      setCurrent(session_id);
      setFromLanding(true);
      await doSend(session_id, text, files);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  // 移除已上传文件：乐观移除 UI，后端按路径删除
  const removeFile = async (path: string) => {
    if (!current) return;
    setFiles((fs) => fs.filter((x) => x.path !== path));
    try {
      await apiDeleteFile(current, path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const send = (text?: string, resume?: string) => {
    if (!current) return;
    void doSend(current, text ?? input, [], resume);
  };

  const doSend = async (sid: string, question: string, uploads: File[], resume?: string) => {
    if (!question.trim() || thinking) return;
    // 本次请求的序号与 AbortController：会话切换后失效
    const seq = ++sendSeq.current;
    const controller = new AbortController();
    abortRef.current = controller;
    const alive = () => seq === sendSeq.current;

    setInput("");
    setError("");
    setStage(null);
    setThinking(true);
    // resume 分支：question 即用户对澄清的回答，同样入消息流
    setMessages((m) => [...m, { kind: "user", text: question }]);
    if (resume) setPendingClarify(null);
    for (const f of uploads) {
      if (!alive()) return;
      try {
        const path = await uploadFile(sid, f);
        if (!alive()) return;
        setFiles((fs) => [...fs, { filename: f.name, path }]);
      } catch (err) {
        if (!alive()) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    }
    // 执行轨迹实时化：progress/tool 事件即时入状态，thinking 期间可见
    setLiveTrace([]);
    liveTraceRef.current = [];
    pendingToolsRef.current = [];
    try {
      for await (const ev of chatStream(sid, { question, resume }, controller.signal)) {
        if (!alive()) return;
        if (ev.type === "clarification") {
          setPendingClarify(ev.question);
          setMessages((m) => [...m, { kind: "agent", text: ev.question }]);
        } else if (ev.type === "result") {
          setMessages((m) => [
            ...m,
            {
              kind: "result",
              data: ev,
              trace: liveTraceRef.current.length ? [...liveTraceRef.current] : undefined,
            },
          ]);
        } else if (ev.type === "progress") {
          setStage(ev.stage);
          if (ev.detail) {
            // 工具调用先于所属节点 progress 到达：缓存挂到新节点条目内
            const entry: TraceEntry = { stage: ev.stage, detail: ev.detail, skipped: ev.skipped };
            if (pendingToolsRef.current.length) entry.tools = [...pendingToolsRef.current];
            pendingToolsRef.current = [];
            setLiveTrace((t) => {
              const next = [...t, entry];
              liveTraceRef.current = next;
              return next;
            });
          }
        } else if (ev.type === "tool") {
          const d = ev.detail;
          if (d.kind === "call") {
            pendingToolsRef.current.push({ tool: d.tool, args: d.args });
          } else {
            // 结果配对到最近一个同名未完成的调用
            const last = [...pendingToolsRef.current]
              .reverse()
              .find((t) => t.tool === d.tool && t.content === undefined);
            if (last) last.content = d.content;
            else pendingToolsRef.current.push({ tool: d.tool, content: d.content });
          }
        } else {
          setError(ev.detail);
        }
      }
    } catch (err) {
      if (!alive()) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive()) setThinking(false);
    }
  };

  // 会话历史抽屉：左上角按钮 + 滑入面板（首页与聊天视图共用）
  const historyUI = (
    <>
      <button
        onClick={() => setHistoryOpen((v) => !v)}
        title="会话历史"
        aria-label="会话历史"
        className="fixed left-4 top-4 z-50 rounded-lg border border-edge bg-panel/70 p-2 text-fg-muted backdrop-blur transition hover:text-accent-fg"
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      {historyOpen && (
        <>
          <aside className="fixed bottom-4 left-4 top-16 z-50 flex w-64 animate-panel-in flex-col rounded-2xl border border-edge bg-panel/95 shadow-[0_8px_40px_rgba(0,0,0,0.4)] backdrop-blur-xl">
            <div className="flex items-center gap-2.5 border-b border-edge p-4">
              <Logo className="h-6 w-6" />
              <span className="text-[15px] font-bold tracking-wide">
                <span className="text-gradient">数枢</span>
              </span>
            </div>
            <button
              onClick={() => {
                newSession();
                setHistoryOpen(false);
              }}
              className="btn-primary m-3 rounded-lg py-2 text-sm font-semibold text-white"
            >
              + 新建会话
            </button>
            <div className="flex-1 overflow-y-auto px-2 pb-2">
              {sessions.map((s) => {
                // 兼容旧后端字符串响应（重启后端后不会再出现）
                const sid = typeof s === "string" ? s : s.session_id;
                const title = typeof s === "string" ? "" : s.title;
                return (
                  <button
                    key={sid}
                    onClick={() => {
                      openSession(sid);
                      setHistoryOpen(false);
                    }}
                    className={`w-full truncate border-l-2 px-3 py-2.5 text-left text-[13px] transition ${
                      current === sid
                        ? "border-cyan-400 bg-cyan-400/10 text-accent-fg"
                        : "border-transparent text-fg-faint hover:bg-fg-strong/4 hover:text-fg-strong"
                    }`}
                  >
                    {title || sid.slice(0, 12)}
                  </button>
                );
              })}
            </div>
          </aside>
        </>
      )}
    </>
  );

  // 首页：无当前会话（首屏 / 点击"新建会话"后）
  if (!current) {
    return (
      <>
        {historyUI}
        <div className={`transition-[margin] duration-300 ${historyOpen ? "ml-72" : ""}`}>
          <Landing busy={starting} error={error} onStart={startFromLanding} />
        </div>
      </>
    );
  }

  const downloadCsv = (data: ResultEvent) => {
    const blob = new Blob([toCsv(data.columns, data.rows)], {
      type: "text/csv;charset=utf-8",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "result.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="flex h-screen animate-fade-up">
      {historyUI}

      {/* 聊天区：历史面板展开时右移让位 */}
      <main
        className={`relative flex min-w-0 flex-1 flex-col transition-[margin] duration-300 ${
          historyOpen ? "ml-72" : ""
        }`}
      >
        {/* 消息流：内层右移 9px（滚动条宽度）放滚动条，内容列与输入卡严格同宽 */}
        <div className="flex-1 overflow-hidden px-5 py-6 pb-64">
          <div className="h-full w-[calc(100%+9px)] overflow-y-auto pr-[9px]">
            <div className="mx-auto flex max-w-3xl flex-col gap-5">
          {messages.map((m, i) =>
            m.kind === "user" ? (
              <div key={i} className="flex animate-fade-up justify-end">
                <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-gradient-to-br from-cyan-500 to-blue-600 px-4 py-2.5 text-sm text-white shadow-[0_0_22px_rgba(34,211,238,0.25)]">
                  {m.text}
                </div>
              </div>
            ) : m.kind === "agent" ? (
              <div key={i} className="flex animate-fade-up justify-start">
                <div className="max-w-[75%] rounded-2xl rounded-bl-md border border-warn-edge bg-warn-soft px-4 py-3 text-sm text-warn-fg/90 shadow-[0_0_18px_rgba(251,191,36,0.07)]">
                  <p className="mb-1 text-xs font-semibold tracking-wide text-warn-fg">
                    🤔 AGENT 需要确认
                  </p>
                  {m.text}
                </div>
              </div>
            ) : (
              <ResultBubble
                key={i}
                data={m.data}
                trace={m.trace}
                onDownload={() => downloadCsv(m.data)}
              />
            ),
          )}
          {thinking && (
            <>
              <div className="flex justify-start" aria-live="polite">
                <div className="relative overflow-hidden rounded-2xl rounded-bl-md border border-cyan-400/20 bg-panel/80 px-4 py-2.5 font-mono text-sm text-accent-fg/80 shadow-[0_0_18px_rgba(34,211,238,0.08)]">
                  {stage ? (
                    <>
                      <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]" />
                      {stage}…<span className="animate-blink">▍</span>
                    </>
                  ) : (
                    <>
                      Agent 思考中<span className="animate-blink">▍</span>
                    </>
                  )}
                  <span className="scanline" />
                </div>
              </div>
              <TracePanel trace={liveTrace} />
            </>
          )}
          {error && (
            <p className="px-1 font-mono text-sm text-danger-fg" role="alert">
              ⚠ {error}
            </p>
          )}
          <div ref={bottomRef} />
            </div>
          </div>
        </div>

        {/* 悬浮输入卡片（DeepSeek 聊天页式：紧凑单行卡 + 发丝边框 + 圆形按钮） */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-2.5 px-5 pb-5">
          {pendingClarify && (
            <div className="w-full max-w-3xl rounded-xl border border-warn-edge bg-warn-soft px-4 py-2 font-mono text-xs text-warn-fg">
              ▸ 请回答 Agent 的问题后发送（将作为澄清继续分析）
            </div>
          )}
          <div
            className={`pointer-events-auto w-full max-w-3xl rounded-3xl border border-edge bg-panel/90 p-3 shadow-[0_4px_16px_rgba(0,0,0,0.15)] backdrop-blur-xl transition focus-within:border-cyan-400/50 focus-within:shadow-[0_0_24px_rgba(34,211,238,0.14)] ${
              fromLanding ? "animate-input-drop" : ""
            }`}
          >
            {files.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {files.map((f) => (
                  <span
                    key={f.path}
                    title={f.path}
                    className="flex items-center gap-1.5 rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 font-mono text-xs text-accent-fg"
                  >
                    {f.filename}
                    <button
                      onClick={() => removeFile(f.path)}
                      title="移除文件"
                      aria-label={`移除文件 ${f.filename}`}
                      className="flex h-4 w-4 items-center justify-center rounded-full transition hover:bg-danger-fg/25 hover:text-danger-fg"
                    >
                      <svg
                        width="8"
                        height="8"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="3.5"
                        strokeLinecap="round"
                        aria-hidden="true"
                      >
                        <path d="M18 6 6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              ref={taRef}
              rows={2}
              className="min-h-[60px] max-h-72 w-full resize-none bg-transparent px-0 py-2 text-base leading-relaxed placeholder:text-fg-faint focus:outline-none"
              placeholder={
                pendingClarify
                  ? "回答 Agent 的问题…"
                  : current
                    ? "输入你的分析问题…"
                    : "请先创建或选择一个会话…"
              }
              value={input}
              disabled={!current || thinking}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(input, pendingClarify ? input : undefined);
                }
              }}
            />
            <div className="mt-1 flex items-center justify-between">
              <label
                title="上传文件"
                aria-label="上传文件"
                className={`flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full border border-edge bg-ink/50 font-mono text-base transition ${
                  current && !thinking
                    ? "text-fg hover:border-cyan-400/40 hover:text-accent-fg"
                    : "cursor-not-allowed text-fg-faint"
                }`}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
                </svg>
                <input
                  type="file"
                  multiple
                  className="sr-only"
                  onChange={onUpload}
                  disabled={!current || thinking}
                  accept=".csv,.json,.db,.sqlite,.sqlite3,.md,.markdown,.txt,.pdf,.mp4"
                />
              </label>
              <button
                onClick={() => send(input, pendingClarify ? input : undefined)}
                disabled={!current || thinking || !input.trim()}
                title="发送 (Enter)"
                aria-label="发送"
                className="btn-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white disabled:opacity-35 disabled:shadow-none"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 19V5m-7 7 7-7 7 7" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function ResultBubble({
  data,
  trace,
  onDownload,
}: {
  data: ResultEvent;
  trace?: TraceEntry[];
  onDownload: () => void;
}) {
  const rows = data.rows.slice(0, MAX_TABLE_ROWS);
  const total = data.total_rows ?? data.rows.length;
  return (
    <div className="flex animate-fade-up justify-start">
      <div className="max-w-[88%] min-w-0 overflow-hidden rounded-2xl rounded-bl-md border border-edge bg-panel/85 shadow-[0_0_30px_rgba(0,0,0,0.35)] backdrop-blur-xl">
        <div className="border-b border-edge/70 px-4 py-3.5">
          <p className="mb-1.5 font-mono text-[10px] tracking-[0.3em] text-accent-fg/70">
            RESULT
          </p>
          <p className="whitespace-pre-wrap text-sm text-fg-strong">
            {data.narration || "（无叙述）"}
          </p>
        </div>
        {data.chart && (
          <div className="border-b border-edge/70 px-4 py-3">
            <ChartBox chart={data.chart} />
          </div>
        )}
        {data.columns.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="text-sm">
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th
                        key={c}
                        className="border-b border-cyan-400/15 bg-fg-strong/8 px-3.5 py-2.5 text-left font-mono text-xs font-medium tracking-wide text-accent-fg"
                      >
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-fg-strong/5 transition last:border-0 odd:bg-fg-strong/2 hover:bg-accent-fg/8"
                    >
                      {data.columns.map((_, j) => (
                        <td key={j} className="px-3.5 py-2 font-mono text-[13px] text-fg">
                          {r[j] === null || r[j] === undefined ? (
                            <span className="text-fg-faint">NULL</span>
                          ) : (
                            String(r[j])
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-edge/70 px-4 py-2.5">
              <span className="font-mono text-xs text-fg-faint">
                共 {total} 行{total > MAX_TABLE_ROWS ? `（仅显示前 ${MAX_TABLE_ROWS} 行）` : ""}
              </span>
              <button
                onClick={onDownload}
                className="rounded-md border border-cyan-400/30 px-2.5 py-1 text-xs text-accent-fg transition hover:bg-cyan-400/10"
              >
                下载 CSV
              </button>
            </div>
          </>
        )}
        {trace && trace.length > 0 && <TracePanel trace={trace} collapsed />}
      </div>
    </div>
  );
}

function TracePanel({ trace, collapsed = false }: { trace: TraceEntry[]; collapsed?: boolean }) {
  if (!trace.length) return null;
  const nodes = (
    <div className="space-y-1.5">
      {trace.map((t, i) =>
        t.skipped ? (
          <div
            key={i}
            className="rounded-md border border-edge/40 bg-ink/20 px-2.5 py-1 font-mono text-[11px] text-fg-faint"
          >
            ⏭ {t.stage} <span className="opacity-60">({t.detail.node} · 无数据，跳过)</span>
          </div>
        ) : (
        <details key={i} className="rounded-md border border-edge/60 bg-ink/40 px-2.5 py-1.5">
          <summary className="cursor-pointer select-none font-mono text-[11px] text-accent-fg/80">
            {t.stage} <span className="text-fg-faint">({t.detail.node})</span>
            {t.tools && t.tools.length > 0 && (
              <span className="text-fg-faint"> · {t.tools.length} 次工具调用</span>
            )}
          </summary>
          <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-fg-muted">
{JSON.stringify({ input: t.detail.input, output: t.detail.output }, null, 2)}
          </pre>
          {t.tools && t.tools.length > 0 && (
            <div className="mt-1.5 space-y-1 border-t border-edge/50 pt-1.5">
              {t.tools.map((tp, j) => (
                <div key={j} className="rounded border border-cyan-400/10 bg-cyan-400/5 px-2 py-1">
                  <div className="font-mono text-[10px] text-accent-fg/80">🔧 {tp.tool}</div>
                  {tp.args !== undefined && (
                    <pre className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-fg-muted">
{JSON.stringify(tp.args, null, 2)}
                    </pre>
                  )}
                  {tp.content !== undefined && (
                    <div className="mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-fg-faint">
                      ↩ {String(tp.content).slice(0, 400)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </details>
        ),
      )}
    </div>
  );

  if (collapsed) {
    return (
      <div className="border-t border-edge/70 px-4 py-2.5">
        <details>
          <summary className="cursor-pointer select-none font-mono text-xs text-fg-muted transition hover:text-accent-fg">
            ▸ 执行轨迹（{trace.length} 个节点）
          </summary>
          <div className="mt-2">{nodes}</div>
        </details>
      </div>
    );
  }

  // 实时模式：thinking 期间随节点完成即时渲染
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[88%] min-w-0 rounded-2xl rounded-bl-md border border-edge/70 bg-panel/70 px-3 py-2.5">
        <p className="mb-1.5 font-mono text-[10px] tracking-[0.3em] text-accent-fg/70">
          TRACE
        </p>
        {nodes}
      </div>
    </div>
  );
}

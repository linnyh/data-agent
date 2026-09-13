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
import Settings from "./Settings";

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
  // 实时执行轨迹(thinking 期间渲染;result 后转入结果气泡折叠区)
  const [liveTrace, setLiveTrace] = useState<TraceEntry[]>([]);
  const liveTraceRef = useRef<TraceEntry[]>([]);
  const pendingToolsRef = useRef<ToolPair[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  // 会话切换竞态防护:序号守卫 + 中止进行中的 SSE 流
  const sendSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // 切换会话/回首页:使进行中的流失效(迟到事件不再写入新视图)
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

  // textarea 随内容自适应高度(上限 max-h-72)
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

  // 显式新建:回到首页,首条分析目标发送时才隐式创建会话
  const newSession = () => {
    invalidateStream();
    setCurrent(null);
    setMessages([]);
    setFiles([]);
    setPendingClarify(null);
    setError("");
    setInput("");
    setStage(null);
  };

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!current) {
      setError("请先创建或选择一个会话,再上传文件");
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

  // 首页首条分析目标:隐式创建会话 → 进入聊天视图 → 上传暂存文件并开始分析
  const startFromLanding = async (text: string, files: File[]) => {
    setStarting(true);
    setError("");
    try {
      const { session_id } = await apiCreateSession();
      setSessions((s) => [{ session_id, title: text.slice(0, 24) }, ...s]);
      setCurrent(session_id);
      await doSend(session_id, text, files);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  // 移除已上传文件:乐观移除 UI,后端按路径删除
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
    // 本次请求的序号与 AbortController:会话切换后失效
    const seq = ++sendSeq.current;
    const controller = new AbortController();
    abortRef.current = controller;
    const alive = () => seq === sendSeq.current;

    setInput("");
    setError("");
    setStage(null);
    setThinking(true);
    // resume 分支:question 即用户对澄清的回答,同样入消息流
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
    // 执行轨迹实时化:progress/tool 事件即时入状态,thinking 期间可见
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
            // 工具调用先于所属节点 progress 到达:缓存挂到新节点条目内
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

  const currentTitle =
    sessions.find((s) => (typeof s === "string" ? s : s.session_id) === current)?.title ?? "新会话";

  return (
    <div className="flex h-screen overflow-hidden rounded-[10px]">
      {/* 侧边栏:标题栏(拖拽) + 新建 + 会话列表 + 设置 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-edge bg-panel/80">
        <div className="drag-region flex h-14 shrink-0 items-center gap-2.5 px-4">
          <Logo className="h-6 w-6" />
          <span className="text-[15px] font-semibold tracking-wide text-fg-strong">数枢</span>
        </div>
        <div className="px-3">
          <button
            onClick={newSession}
            className="btn-primary w-full rounded-lg py-1.5 text-[13px] font-medium text-white"
          >
            + 新建会话
          </button>
        </div>
        <div className="mt-3 flex-1 overflow-y-auto px-2 pb-2">
          {sessions.map((s) => {
            // 兼容旧后端字符串响应(重启后端后不会再出现)
            const sid = typeof s === "string" ? s : s.session_id;
            const title = typeof s === "string" ? "" : s.title;
            const active = current === sid;
            return (
              <button
                key={sid}
                onClick={() => openSession(sid)}
                className={`w-full truncate rounded-lg px-3 py-2 text-left text-[13px] transition ${
                  active
                    ? "bg-accent-soft text-fg-strong"
                    : "text-fg-muted hover:bg-ink/50 hover:text-fg"
                }`}
              >
                {title || sid.slice(0, 12)}
              </button>
            );
          })}
          {sessions.length === 0 && (
            <p className="px-3 py-2 text-xs text-fg-faint">暂无会话</p>
          )}
        </div>
        <div className="border-t border-edge p-2">
          <Settings
            triggerClassName="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[13px] text-fg-muted transition hover:bg-ink/50 hover:text-fg"
          />
        </div>
      </aside>

      {/* 主区 */}
      <main className="relative flex min-w-0 flex-1 flex-col bg-ink">
        {/* 顶部标题栏:Overlay 拖拽区,显示当前会话标题 */}
        <div className="drag-region flex h-14 shrink-0 items-center justify-center border-b border-edge/60">
          <span className="text-[13px] font-medium text-fg-muted">
            {current ? currentTitle : "数枢 · 数据分析助手"}
          </span>
        </div>

        {!current ? (
          <Landing busy={starting} error={error} onStart={startFromLanding} />
        ) : (
          <>
            {/* 消息流:内层右移 9px(滚动条宽度)放滚动条,内容列与输入卡严格同宽 */}
            <div className="flex-1 overflow-hidden px-5 py-6 pb-40">
              <div className="h-full w-[calc(100%+9px)] overflow-y-auto pr-[9px]">
                <div className="mx-auto flex max-w-3xl flex-col gap-4">
                  {messages.map((m, i) =>
                    m.kind === "user" ? (
                      <div key={i} className="flex animate-fade-up justify-end">
                        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[14px] text-white">
                          {m.text}
                        </div>
                      </div>
                    ) : m.kind === "agent" ? (
                      <div key={i} className="flex animate-fade-up justify-start">
                        <div className="max-w-[75%] rounded-2xl rounded-bl-md border border-warn-edge bg-warn-soft px-4 py-3 text-[14px] text-warn-fg">
                          <p className="mb-1 text-xs font-semibold text-warn-fg">
                            Agent 需要确认
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
                        <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-edge bg-panel px-4 py-2.5 text-[14px] text-fg-muted">
                          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                          {stage ? `${stage}…` : "思考中"}
                          <span className="animate-blink">▍</span>
                        </div>
                      </div>
                      <TracePanel trace={liveTrace} />
                    </>
                  )}
                  {error && (
                    <p className="px-1 font-mono text-[13px] text-danger-fg" role="alert">
                      ⚠ {error}
                    </p>
                  )}
                  <div ref={bottomRef} />
                </div>
              </div>
            </div>

            {/* 悬浮输入卡片 */}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col items-center gap-2.5 px-5 pb-5">
              {pendingClarify && (
                <div className="w-full max-w-3xl rounded-lg border border-warn-edge bg-warn-soft px-4 py-2 font-mono text-xs text-warn-fg">
                  ▸ 请回答 Agent 的问题后发送(将作为澄清继续分析)
                </div>
              )}
              <div className="pointer-events-auto w-full max-w-3xl rounded-2xl border border-edge bg-panel/90 p-3 shadow-lg backdrop-blur-xl transition focus-within:border-accent/50">
                {files.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {files.map((f) => (
                      <span
                        key={f.path}
                        title={f.path}
                        className="flex items-center gap-1.5 rounded-md border border-edge bg-accent-soft px-2.5 py-0.5 font-mono text-xs text-accent-fg"
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
                  className="min-h-[60px] max-h-72 w-full resize-none bg-transparent px-0 py-2 text-[15px] leading-relaxed placeholder:text-fg-faint focus:outline-none"
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
                    className={`flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md border border-edge bg-ink/50 font-mono text-base transition ${
                      current && !thinking
                        ? "text-fg hover:border-accent/50 hover:text-accent-fg"
                        : "cursor-not-allowed text-fg-faint"
                    }`}
                  >
                    <svg
                      width="15"
                      height="15"
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
                    className="btn-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-white disabled:opacity-35"
                  >
                    <svg
                      width="15"
                      height="15"
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
          </>
        )}
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
      <div className="max-w-[88%] min-w-0 overflow-hidden rounded-2xl rounded-bl-md border border-edge bg-panel shadow-sm">
        <div className="border-b border-edge/60 px-4 py-3.5">
          <p className="mb-1.5 font-mono text-[10px] font-medium tracking-[0.2em] text-fg-faint">
            RESULT
          </p>
          <p className="whitespace-pre-wrap text-[14px] text-fg-strong">
            {data.narration || "(无叙述)"}
          </p>
        </div>
        {data.chart && (
          <div className="border-b border-edge/60 px-4 py-3">
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
                        className="border-b border-edge bg-ink/40 px-3.5 py-2.5 text-left font-mono text-xs font-medium text-fg-muted"
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
                      className="border-b border-edge/40 transition last:border-0 hover:bg-ink/30"
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
            <div className="flex items-center justify-between border-t border-edge/60 px-4 py-2.5">
              <span className="font-mono text-xs text-fg-faint">
                共 {total} 行{total > MAX_TABLE_ROWS ? `(仅显示前 ${MAX_TABLE_ROWS} 行)` : ""}
              </span>
              <button
                onClick={onDownload}
                className="rounded-md border border-edge px-2.5 py-1 text-xs text-accent-fg transition hover:bg-accent-soft"
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
            className="rounded-md border border-edge/50 bg-ink/30 px-2.5 py-1 font-mono text-[11px] text-fg-faint"
          >
            ⏭ {t.stage} <span className="opacity-60">({t.detail.node} · 无数据,跳过)</span>
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
                <div key={j} className="rounded border border-edge/50 bg-ink/30 px-2 py-1">
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
      <div className="border-t border-edge/60 px-4 py-2.5">
        <details>
          <summary className="cursor-pointer select-none font-mono text-xs text-fg-muted transition hover:text-accent-fg">
            ▸ 执行轨迹({trace.length} 个节点)
          </summary>
          <div className="mt-2">{nodes}</div>
        </details>
      </div>
    );
  }

  // 实时模式:thinking 期间随节点完成即时渲染
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[88%] min-w-0 rounded-2xl rounded-bl-md border border-edge bg-panel/70 px-3 py-2.5">
        <p className="mb-1.5 font-mono text-[10px] font-medium tracking-[0.2em] text-fg-faint">
          TRACE
        </p>
        {nodes}
      </div>
    </div>
  );
}

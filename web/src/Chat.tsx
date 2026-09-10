import { useEffect, useRef, useState } from "react";
import {
  apiCreateSession,
  apiHistory,
  apiListSessions,
  chatStream,
  clearToken,
  HistoryRecord,
  ResultEvent,
  toCsv,
  uploadFile,
} from "./api";
import Logo from "./Logo";

type Message =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string } // clarify 提问
  | { kind: "result"; data: ResultEvent };

const MAX_TABLE_ROWS = 50;

export default function Chat() {
  const [sessions, setSessions] = useState<string[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [pendingClarify, setPendingClarify] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiListSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  const openSession = async (sid: string) => {
    setCurrent(sid);
    setFiles([]);
    setPendingClarify(null);
    setError("");
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
            },
          } as Message,
        ]),
      );
    } catch {
      setMessages([]);
    }
  };

  const newSession = async () => {
    try {
      const { session_id } = await apiCreateSession();
      setSessions((s) => [session_id, ...s]);
      setCurrent(session_id);
      setMessages([]);
      setFiles([]);
      setPendingClarify(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
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
        await uploadFile(current, f);
        setFiles((fs) => [...fs, f.name]);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  };

  const send = async (text?: string, resume?: string) => {
    const question = text ?? input;
    if (!current || !question.trim() || thinking) return;
    setInput("");
    setError("");
    setStage(null);
    setThinking(true);
    // resume 分支：question 即用户对澄清的回答，同样入消息流
    setMessages((m) => [...m, { kind: "user", text: question }]);
    if (resume) setPendingClarify(null);
    try {
      for await (const ev of chatStream(current, { question, resume })) {
        if (ev.type === "clarification") {
          setPendingClarify(ev.question);
          setMessages((m) => [...m, { kind: "agent", text: ev.question }]);
        } else if (ev.type === "result") {
          setMessages((m) => [...m, { kind: "result", data: ev }]);
        } else if (ev.type === "progress") {
          setStage(ev.stage);
        } else {
          setError(ev.detail);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setThinking(false);
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

  return (
    <div className="flex h-screen">
      {/* 左侧会话栏 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-edge bg-panel/60 backdrop-blur-xl">
        <div className="flex items-center justify-between border-b border-edge p-4">
          <div className="flex items-center gap-2.5">
            <Logo className="h-6 w-6" />
            <span className="text-[15px] font-bold tracking-wide">
              <span className="text-gradient">数据分析 Agent</span>
            </span>
          </div>
          <button
            onClick={() => {
              clearToken();
              window.dispatchEvent(new Event("da:unauthorized"));
            }}
            className="text-xs text-slate-500 transition hover:text-cyan-300"
          >
            退出
          </button>
        </div>
        <button
          onClick={newSession}
          className="btn-primary m-3 rounded-lg py-2 text-sm font-semibold text-white"
        >
          + 新建会话
        </button>
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {sessions.map((sid) => (
            <button
              key={sid}
              onClick={() => openSession(sid)}
              className={`w-full truncate border-l-2 px-3 py-2.5 text-left font-mono text-[13px] transition ${
                current === sid
                  ? "border-cyan-400 bg-cyan-400/10 text-cyan-200"
                  : "border-transparent text-slate-500 hover:bg-white/[0.04] hover:text-slate-200"
              }`}
            >
              {sid.slice(0, 12)}
            </button>
          ))}
        </div>
      </aside>

      {/* 右侧聊天区 */}
      <main className="flex min-w-0 flex-1 flex-col">
        {/* 文件标签 */}
        {current && files.length > 0 && (
          <div className="flex items-center gap-2 border-b border-edge bg-panel/40 px-4 py-2.5 text-xs backdrop-blur">
            <span className="text-slate-500">数据文件:</span>
            {files.map((f) => (
              <span
                key={f}
                className="rounded-md border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 font-mono text-cyan-300"
              >
                {f}
              </span>
            ))}
          </div>
        )}

        {/* 消息流 */}
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-6">
          {messages.length === 0 && !thinking && (
            <div className="mt-24 flex flex-col items-center text-center">
              <Logo className="h-16 w-16 opacity-80" />
              <p className="mt-5 text-sm text-slate-400">
                上传数据文件后，输入你的分析问题开始对话
              </p>
              <p className="mt-2 font-mono text-[11px] tracking-[0.25em] text-slate-600">
                CSV · JSON · SQLITE · PDF · MP4
              </p>
            </div>
          )}
          {messages.map((m, i) =>
            m.kind === "user" ? (
              <div key={i} className="flex animate-fade-up justify-end">
                <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-gradient-to-br from-cyan-500 to-blue-600 px-4 py-2.5 text-sm text-white shadow-[0_0_22px_rgba(34,211,238,0.25)]">
                  {m.text}
                </div>
              </div>
            ) : m.kind === "agent" ? (
              <div key={i} className="flex animate-fade-up justify-start">
                <div className="max-w-[75%] rounded-2xl rounded-bl-md border border-amber-400/25 bg-amber-400/[0.08] px-4 py-3 text-sm text-amber-100/90 shadow-[0_0_18px_rgba(251,191,36,0.07)]">
                  <p className="mb-1 text-xs font-semibold tracking-wide text-amber-300">
                    🤔 AGENT 需要确认
                  </p>
                  {m.text}
                </div>
              </div>
            ) : (
              <ResultBubble key={i} data={m.data} onDownload={() => downloadCsv(m.data)} />
            ),
          )}
          {thinking && (
            <div className="flex justify-start">
              <div className="relative overflow-hidden rounded-2xl rounded-bl-md border border-cyan-400/20 bg-panel/80 px-4 py-2.5 font-mono text-sm text-cyan-300/80 shadow-[0_0_18px_rgba(34,211,238,0.08)]">
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
          )}
          {error && <p className="px-1 font-mono text-sm text-red-400">⚠ {error}</p>}
          <div ref={bottomRef} />
        </div>

        {/* 输入区 */}
        <div className="border-t border-edge bg-panel/60 p-3.5 backdrop-blur-xl">
          {pendingClarify && (
            <p className="mb-2 font-mono text-xs text-amber-300/90">
              ▸ 请回答 Agent 的问题后发送（将作为澄清继续分析）
            </p>
          )}
          <div className="flex items-center gap-2.5">
            <label
              className={`cursor-pointer rounded-lg border border-edge bg-ink/50 px-3.5 py-2.5 font-mono text-sm transition ${
                current && !thinking
                  ? "text-slate-300 hover:border-cyan-400/40 hover:text-cyan-300"
                  : "cursor-not-allowed text-slate-600"
              }`}
            >
              ⇪ 上传
              <input
                type="file"
                multiple
                className="hidden"
                onChange={onUpload}
                disabled={!current || thinking}
                accept=".csv,.json,.db,.sqlite,.sqlite3,.md,.markdown,.txt,.pdf,.mp4"
              />
            </label>
            <input
              className="flex-1 rounded-lg border border-edge bg-ink/60 px-3.5 py-2.5 text-sm transition placeholder:text-slate-500 focus:border-cyan-400/60 focus:shadow-[0_0_18px_rgba(34,211,238,0.12)] focus:outline-none"
              placeholder={
                pendingClarify
                  ? "回答 Agent 的问题…"
                  : current
                    ? "输入你的分析问题…"
                    : "请先创建或选择一个会话"
              }
              value={input}
              disabled={!current || thinking}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input, pendingClarify ? input : undefined);
                }
              }}
            />
            <button
              onClick={() => send(input, pendingClarify ? input : undefined)}
              disabled={!current || thinking || !input.trim()}
              className="btn-primary rounded-lg px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-35 disabled:shadow-none"
            >
              发送
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

function ResultBubble({ data, onDownload }: { data: ResultEvent; onDownload: () => void }) {
  const rows = data.rows.slice(0, MAX_TABLE_ROWS);
  const total = data.total_rows ?? data.rows.length;
  return (
    <div className="flex animate-fade-up justify-start">
      <div className="max-w-[88%] min-w-0 overflow-hidden rounded-2xl rounded-bl-md border border-edge bg-panel/85 shadow-[0_0_30px_rgba(0,0,0,0.35)] backdrop-blur-xl">
        <div className="border-b border-edge/70 px-4 py-3.5">
          <p className="mb-1.5 font-mono text-[10px] tracking-[0.3em] text-cyan-400/70">
            RESULT
          </p>
          <p className="whitespace-pre-wrap text-sm text-slate-200">
            {data.narration || "（无叙述）"}
          </p>
        </div>
        {data.columns.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="text-sm">
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th
                        key={c}
                        className="border-b border-cyan-400/15 bg-[#0a1526] px-3.5 py-2.5 text-left font-mono text-xs font-medium tracking-wide text-cyan-300"
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
                      className="border-b border-white/[0.04] transition last:border-0 odd:bg-white/[0.02] hover:bg-cyan-400/[0.05]"
                    >
                      {data.columns.map((_, j) => (
                        <td key={j} className="px-3.5 py-2 font-mono text-[13px] text-slate-300">
                          {r[j] === null || r[j] === undefined ? (
                            <span className="text-slate-600">NULL</span>
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
              <span className="font-mono text-xs text-slate-500">
                共 {total} 行{total > MAX_TABLE_ROWS ? `（仅显示前 ${MAX_TABLE_ROWS} 行）` : ""}
              </span>
              <button
                onClick={onDownload}
                className="rounded-md border border-cyan-400/30 px-2.5 py-1 text-xs text-cyan-300 transition hover:bg-cyan-400/10"
              >
                下载 CSV
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

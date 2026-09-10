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
    if (!current) return;
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
    <div className="h-screen flex bg-slate-50">
      {/* 左侧会话栏 */}
      <aside className="w-56 bg-white border-r flex flex-col">
        <div className="p-3 border-b flex items-center justify-between">
          <span className="font-semibold text-sm">数据分析 Agent</span>
          <button
            onClick={() => {
              clearToken();
              window.dispatchEvent(new Event("da:unauthorized"));
            }}
            className="text-xs text-slate-400 hover:text-slate-600"
          >
            退出
          </button>
        </div>
        <button
          onClick={newSession}
          className="m-3 py-2 rounded bg-slate-800 text-white text-sm"
        >
          + 新建会话
        </button>
        <div className="flex-1 overflow-y-auto">
          {sessions.map((sid) => (
            <button
              key={sid}
              onClick={() => openSession(sid)}
              className={`w-full text-left px-4 py-2 text-sm truncate ${
                current === sid ? "bg-slate-100 font-medium" : "hover:bg-slate-50"
              }`}
            >
              {sid.slice(0, 12)}
            </button>
          ))}
        </div>
      </aside>

      {/* 右侧聊天区 */}
      <main className="flex-1 flex flex-col">
        {/* 文件标签 */}
        {current && files.length > 0 && (
          <div className="px-4 py-2 bg-white border-b flex gap-2 items-center text-xs">
            <span className="text-slate-400">数据文件:</span>
            {files.map((f) => (
              <span key={f} className="px-2 py-0.5 rounded bg-blue-50 text-blue-700">
                {f}
              </span>
            ))}
          </div>
        )}

        {/* 消息流 */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.length === 0 && !thinking && (
            <div className="text-center text-slate-400 text-sm mt-20">
              上传数据文件后，输入你的分析问题开始对话
            </div>
          )}
          {messages.map((m, i) =>
            m.kind === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[75%] bg-slate-800 text-white rounded-lg px-4 py-2 text-sm whitespace-pre-wrap">
                  {m.text}
                </div>
              </div>
            ) : m.kind === "agent" ? (
              <div key={i} className="flex justify-start">
                <div className="max-w-[75%] bg-amber-50 border border-amber-200 rounded-lg px-4 py-2 text-sm">
                  <p className="text-amber-700 font-medium mb-1">🤔 Agent 需要确认</p>
                  {m.text}
                </div>
              </div>
            ) : (
              <ResultBubble key={i} data={m.data} onDownload={() => downloadCsv(m.data)} />
            ),
          )}
          {thinking && (
            <div className="flex justify-start">
              <div className="bg-white border rounded-lg px-4 py-2 text-sm text-slate-400 animate-pulse">
                Agent 思考中…
              </div>
            </div>
          )}
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div ref={bottomRef} />
        </div>

        {/* 输入区 */}
        <div className="border-t bg-white p-3">
          {pendingClarify && (
            <p className="text-xs text-amber-600 mb-2">
              请回答 Agent 的问题后发送（将作为澄清继续分析）
            </p>
          )}
          <div className="flex gap-2 items-center">
            <label className="px-3 py-2 border rounded text-sm text-slate-600 hover:bg-slate-50 cursor-pointer">
              上传文件
              <input
                type="file"
                multiple
                className="hidden"
                onChange={onUpload}
                accept=".csv,.json,.db,.sqlite,.sqlite3,.md,.markdown,.txt,.pdf,.mp4"
              />
            </label>
            <input
              className="flex-1 border rounded px-3 py-2 text-sm"
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
              className="px-4 py-2 rounded bg-slate-800 text-white text-sm disabled:opacity-40"
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
    <div className="flex justify-start">
      <div className="max-w-[85%] bg-white border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b bg-slate-50">
          <p className="text-sm text-slate-800 whitespace-pre-wrap">
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
                      <th key={c} className="px-3 py-2 text-left bg-slate-100 border-b font-medium">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b last:border-0">
                      {data.columns.map((_, j) => (
                        <td key={j} className="px-3 py-1.5 text-slate-700">
                          {r[j] === null || r[j] === undefined ? (
                            <span className="text-slate-300">NULL</span>
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
            <div className="px-4 py-2 flex items-center justify-between text-xs text-slate-400">
              <span>
                共 {total} 行{total > MAX_TABLE_ROWS ? `（仅显示前 ${MAX_TABLE_ROWS} 行）` : ""}
              </span>
              <button
                onClick={onDownload}
                className="px-2 py-1 rounded border hover:bg-slate-50 text-slate-600"
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

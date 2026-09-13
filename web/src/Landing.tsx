import { useEffect, useRef, useState } from "react";
import Logo from "./Logo";

export const SUGGESTIONS = [
  "分析各品类的销售额趋势",
  "找出销售额前 10 的产品",
  "对比不同地区的销售表现",
  "统计每月订单量变化",
];

export default function Landing({
  busy,
  error,
  onStart,
}: {
  busy: boolean;
  error: string;
  onStart: (text: string, files: File[]) => void;
}) {
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // textarea 随内容自适应高度(上限 max-h-72)
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 288) + "px";
  }, [input]);

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFiles((fs) => [...fs, ...Array.from(e.target.files || [])]);
    e.target.value = "";
  };

  const send = () => {
    if (!input.trim() || busy) return;
    onStart(input, files);
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {/* 中央内容 */}
      <div className="flex flex-1 flex-col items-center justify-center px-5 pb-10">
        <Logo className="h-14 w-14" />
        <h1 className="mt-5 text-3xl font-bold text-fg-strong">数枢</h1>
        <p className="mt-2 text-sm text-fg-muted">上传数据,用自然语言完成分析</p>
        <p className="mt-2 font-mono text-[11px] tracking-[0.2em] text-fg-faint">
          CSV · JSON · SQLITE · PDF · MP4
        </p>

        {/* 输入卡片 */}
        <div className="mt-8 w-full max-w-3xl rounded-2xl border border-edge bg-panel/90 p-3 shadow-sm backdrop-blur-xl transition focus-within:border-accent/50">
          {files.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {files.map((f) => (
                <span
                  key={f.name + f.size}
                  className="flex items-center gap-1.5 rounded-md border border-edge bg-accent-soft px-2.5 py-0.5 font-mono text-xs text-accent-fg"
                >
                  {f.name}
                  <button
                    onClick={() => setFiles((fs) => fs.filter((x) => x !== f))}
                    title="移除文件"
                    aria-label={`移除文件 ${f.name}`}
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
            className="min-h-[100px] max-h-72 w-full resize-none bg-transparent px-0 py-2 text-[15px] leading-relaxed placeholder:text-fg-faint focus:outline-none"
            placeholder="输入你的分析问题…"
            value={input}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className="mt-1 flex items-center justify-between">
            <label
              title="上传文件"
              aria-label="上传文件"
              className={`flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md border border-edge bg-ink/50 font-mono text-base transition ${
                busy
                  ? "cursor-not-allowed text-fg-faint"
                  : "text-fg hover:border-accent/50 hover:text-accent-fg"
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
                onChange={onPick}
                disabled={busy}
                accept=".csv,.json,.db,.sqlite,.sqlite3,.md,.markdown,.txt,.pdf,.mp4"
              />
            </label>
            <button
              onClick={send}
              disabled={busy || !input.trim()}
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

        {/* 建议提问 */}
        <div className="mt-6 flex max-w-3xl flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setInput(s)}
              disabled={busy}
              className="rounded-full border border-edge bg-panel/60 px-4 py-1.5 text-[13px] text-fg-muted transition hover:border-accent/50 hover:text-accent-fg"
            >
              {s}
            </button>
          ))}
        </div>

        {error && (
          <p className="mt-5 font-mono text-[13px] text-danger-fg" role="alert">
            ⚠ {error}
          </p>
        )}
      </div>
    </div>
  );
}

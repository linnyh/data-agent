import { useState } from "react";
import { apiLogin, apiRegister, setToken } from "./api";
import Logo from "./Logo";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const fn = mode === "login" ? apiLogin : apiRegister;
      const res = await fn(username, password);
      setToken(res.token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-[400px] animate-fade-up rounded-2xl border border-cyan-400/15 bg-panel/70 p-8 backdrop-blur-xl shadow-[0_0_60px_rgba(34,211,238,0.08)]"
      >
        <div className="mb-5 flex justify-center">
          <Logo className="h-14 w-14" />
        </div>
        <h1 className="mb-1 text-center text-2xl font-bold">
          <span className="text-gradient">数据分析 Agent</span>
        </h1>
        <p className="mb-8 text-center font-mono text-[11px] tracking-[0.3em] text-slate-500">
          DATA ANALYSIS AGENT
        </p>

        <div className="mb-6 flex rounded-lg border border-edge bg-ink/60 p-1">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md py-2 text-sm transition ${
                mode === m
                  ? "border border-cyan-400/30 bg-gradient-to-r from-cyan-500/20 to-blue-500/20 text-cyan-300"
                  : "border border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <input
          className="mb-3 w-full rounded-lg border border-edge bg-ink/70 px-3 py-2.5 text-sm placeholder:text-slate-500 transition focus:border-cyan-400/60 focus:shadow-[0_0_16px_rgba(34,211,238,0.15)] focus:outline-none"
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <input
          className="mb-4 w-full rounded-lg border border-edge bg-ink/70 px-3 py-2.5 text-sm placeholder:text-slate-500 transition focus:border-cyan-400/60 focus:shadow-[0_0_16px_rgba(34,211,238,0.15)] focus:outline-none"
          placeholder="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="mb-3 font-mono text-sm text-red-400">⚠ {error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="btn-primary w-full rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-40 disabled:shadow-none"
        >
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册"}
        </button>
      </form>
    </div>
  );
}

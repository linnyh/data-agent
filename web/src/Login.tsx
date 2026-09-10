import { useState } from "react";
import { apiLogin, apiRegister, setToken } from "./api";

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
    <div className="min-h-screen flex items-center justify-center bg-slate-100">
      <form onSubmit={submit} className="bg-white rounded-lg shadow p-8 w-96">
        <h1 className="text-xl font-bold mb-6 text-center">数据分析 Agent</h1>
        <div className="flex mb-6 rounded-lg overflow-hidden border">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`flex-1 py-2 text-sm ${
                mode === m ? "bg-slate-800 text-white" : "bg-white text-slate-600"
              }`}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>
        <input
          className="w-full border rounded px-3 py-2 mb-3 text-sm"
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <input
          className="w-full border rounded px-3 py-2 mb-4 text-sm"
          placeholder="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-red-600 text-sm mb-3">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="w-full bg-slate-800 text-white rounded py-2 text-sm disabled:opacity-50"
        >
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册"}
        </button>
      </form>
    </div>
  );
}

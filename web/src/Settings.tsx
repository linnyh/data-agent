import { useEffect, useState } from "react";
import { apiGetSettings, apiSaveSettings } from "./api";

export type ThemePref = "light" | "dark" | "system";

export const THEME_KEY = "da:theme";

export function currentPref(): ThemePref {
  const v = localStorage.getItem(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function applyTheme(pref: ThemePref) {
  const light = pref === "system"
    ? window.matchMedia("(prefers-color-scheme: light)").matches
    : pref === "light";
  document.documentElement.classList.toggle("light", light);
}

type FieldDef = {
  key: string;
  label: string;
  secret?: boolean;
  placeholder?: string;
};

// 字段分组与中文说明(键集合与后端 SETTINGS_KEYS 白名单一致)
const GROUPS: { title: string; desc: string; fields: FieldDef[] }[] = [
  {
    title: "主模型",
    desc: "文本分析所用的 OpenAI 兼容模型",
    fields: [
      { key: "MODEL_API_URL", label: "API 地址", placeholder: "https://api.deepseek.com" },
      { key: "MODEL_API_KEY", label: "API Key", secret: true },
      { key: "MODEL_NAME", label: "模型名", placeholder: "deepseek-v4-flash" },
    ],
  },
  {
    title: "视频链视觉模型",
    desc: "视频帧 / 截图理解;留空则回退主模型配置",
    fields: [
      { key: "VIDEO_MODEL_NAME", label: "模型名" },
      { key: "VIDEO_MODEL_API_URL", label: "API 地址" },
      { key: "VIDEO_MODEL_API_KEY", label: "API Key", secret: true },
    ],
  },
  {
    title: "LangSmith 追踪",
    desc: "可选:执行轨迹上报,便于调试",
    fields: [
      { key: "LANGSMITH_TRACING", label: "开启追踪", placeholder: "false" },
      { key: "LANGSMITH_API_KEY", label: "API Key", secret: true },
      { key: "LANGSMITH_PROJECT", label: "项目名" },
    ],
  },
];

const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

export default function Settings({ triggerClassName }: { triggerClassName?: string }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);
  const [pref, setPref] = useState<ThemePref>(currentPref());

  useEffect(() => {
    if (!open || loaded) return;
    apiGetSettings()
      .then((v) => setValues(v))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, [open, loaded]);

  const save = async () => {
    setSaving(true);
    try {
      await apiSaveSettings(values);
      setSavedAt(Date.now());
    } catch (e) {
      alert(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const setTheme = (next: ThemePref) => {
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
    setPref(next);
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="设置"
        aria-label="打开设置"
        className={triggerClassName}
      >
        <GearIcon />
        <span>设置</span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <div
            className="max-h-[85vh] w-[min(560px,92vw)] overflow-y-auto rounded-xl border border-edge bg-panel p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-[15px] font-semibold text-fg-strong">设置</h2>
              <button
                onClick={() => setOpen(false)}
                aria-label="关闭设置"
                className="rounded-md px-2 py-1 text-fg-muted transition hover:text-fg"
              >
                ✕
              </button>
            </div>

            <section className="mb-6">
              <h3 className="mb-1 text-[13px] font-medium text-fg">外观</h3>
              <p className="mb-3 text-xs text-fg-muted">主题模式</p>
              <div className="flex rounded-lg border border-edge bg-ink/60 p-0.5">
                {THEME_OPTIONS.map((o) => (
                  <button
                    key={o.value}
                    onClick={() => setTheme(o.value)}
                    className={`flex-1 rounded-md px-3 py-1.5 text-[13px] transition ${
                      pref === o.value
                        ? "bg-panel text-fg shadow-sm"
                        : "text-fg-muted hover:text-fg"
                    }`}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </section>

            {GROUPS.map((g) => (
              <section key={g.title} className="mb-6">
                <h3 className="mb-1 text-[13px] font-medium text-fg">{g.title}</h3>
                <p className="mb-3 text-xs text-fg-muted">{g.desc}</p>
                <div className="space-y-3">
                  {g.fields.map((f) => (
                    <label key={f.key} className="block">
                      <span className="mb-1 block text-xs text-fg-muted">
                        {f.label}
                        <code className="ml-2 rounded bg-ink/60 px-1 font-mono text-[10px] text-fg-faint">
                          {f.key}
                        </code>
                      </span>
                      <input
                        type={f.secret ? "password" : "text"}
                        value={values[f.key] ?? ""}
                        placeholder={f.placeholder ?? ""}
                        autoComplete="off"
                        spellCheck={false}
                        onChange={(e) =>
                          setValues((v) => ({ ...v, [f.key]: e.target.value }))
                        }
                        className="w-full rounded-lg border border-edge bg-ink/50 px-3 py-2 font-mono text-[13px] text-fg outline-none transition focus:border-accent"
                      />
                    </label>
                  ))}
                </div>
              </section>
            ))}

            <div className="flex items-center justify-end gap-3">
              {savedAt > 0 && (
                <span className="text-xs text-accent-fg">已保存,立即生效</span>
              )}
              <button
                onClick={save}
                disabled={saving}
                className="btn-primary rounded-lg px-4 py-2 text-[13px] font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

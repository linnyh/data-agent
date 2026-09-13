import { useEffect, useState } from "react";
import { apiGetSettings, apiSaveSettings } from "./api";

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

export default function Settings() {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);

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

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="模型与追踪配置"
        aria-label="打开设置"
        className="fixed top-4 right-16 z-50 rounded-lg border border-edge bg-panel/70 p-2 text-fg-muted backdrop-blur transition hover:text-accent-fg"
      >
        <GearIcon />
      </button>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={() => setOpen(false)}
    >
      <div
        className="max-h-[85vh] w-[min(560px,92vw)] overflow-y-auto rounded-2xl border border-edge bg-panel p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-fg">模型与追踪配置</h2>
          <button
            onClick={() => setOpen(false)}
            aria-label="关闭设置"
            className="rounded-md px-2 py-1 text-fg-muted transition hover:text-fg"
          >
            ✕
          </button>
        </div>

        {GROUPS.map((g) => (
          <section key={g.title} className="mb-6">
            <h3 className="mb-1 text-sm font-medium text-accent-fg">{g.title}</h3>
            <p className="mb-3 text-xs text-fg-muted">{g.desc}</p>
            <div className="space-y-3">
              {g.fields.map((f) => (
                <label key={f.key} className="block">
                  <span className="mb-1 block text-xs text-fg-muted">
                    {f.label}
                    <code className="ml-2 rounded bg-panel/60 px-1 font-mono text-[10px] text-fg-muted/70">
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
                    className="w-full rounded-lg border border-edge bg-bg/60 px-3 py-2 font-mono text-sm text-fg outline-none transition focus:border-accent"
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
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg transition hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

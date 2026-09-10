import { useEffect, useState } from "react";

const THEME_KEY = "da:theme";
type Pref = "light" | "dark" | "system";
type Resolved = "light" | "dark";

const prefersLight = () => window.matchMedia("(prefers-color-scheme: light)").matches;

export function currentPref(): Pref {
  const v = localStorage.getItem(THEME_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function resolveTheme(pref: Pref): Resolved {
  return pref === "system" ? (prefersLight() ? "light" : "dark") : pref;
}

export function applyTheme(pref: Pref) {
  const light = resolveTheme(pref) === "light";
  document.documentElement.classList.toggle("light", light);
  // 浏览器地址栏等 UI 颜色随主题
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", light ? "#f4f6fa" : "#05070d");
}

const NEXT: Record<Pref, Pref> = { system: "light", light: "dark", dark: "system" };

export default function ThemeToggle() {
  const [pref, setPref] = useState<Pref>(currentPref());

  // 跟随系统时监听系统主题变化
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const toggle = () => {
    const next = NEXT[pref];
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
    setPref(next);
  };

  const resolved = resolveTheme(pref);
  const title =
    pref === "system"
      ? `主题：跟随系统（当前${resolved === "light" ? "浅色" : "深色"}）`
      : `主题：${resolved === "light" ? "浅色" : "深色"}`;

  return (
    <button
      onClick={toggle}
      title={title}
      aria-label="切换主题"
      className="fixed top-4 right-4 z-50 rounded-lg border border-edge bg-panel/70 p-2 text-fg-muted backdrop-blur transition hover:text-accent-fg"
    >
      {pref === "system" ? <AutoIcon /> : resolved === "light" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function AutoIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none" />
    </svg>
  );
}

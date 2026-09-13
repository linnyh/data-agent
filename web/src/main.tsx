import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Chat from "./Chat";
import Settings from "./Settings";
import ThemeToggle, { applyTheme, currentPref } from "./ThemeToggle";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./index.css";

// 渲染前应用持久化主题（默认跟随系统），避免闪烁
applyTheme(currentPref());

function App() {
  return (
    <>
      <div className="bg-grid" />
      <div className="orb orb-a" />
      <div className="orb orb-b" />
      <ThemeToggle />
      <Settings />
      <Chat />
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

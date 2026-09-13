import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Chat from "./Chat";
import { applyTheme, currentPref } from "./Settings";
import "./index.css";

// 渲染前应用持久化主题(默认跟随系统),避免闪烁
applyTheme(currentPref());

function App() {
  return <Chat />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

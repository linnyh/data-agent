import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { hasToken } from "./api";
import Login from "./Login";
import Chat from "./Chat";
import "@fontsource/space-grotesk/400.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./index.css";

function App() {
  const [authed, setAuthed] = useState(hasToken());

  useEffect(() => {
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener("da:unauthorized", onUnauthorized);
    return () => window.removeEventListener("da:unauthorized", onUnauthorized);
  }, []);

  return (
    <>
      <div className="bg-grid" />
      <div className="orb orb-a" />
      <div className="orb orb-b" />
      {authed ? <Chat /> : <Login onLogin={() => setAuthed(true)} />}
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

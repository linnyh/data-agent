import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { hasToken } from "./api";
import Login from "./Login";
import Chat from "./Chat";
import "./index.css";

function App() {
  const [authed, setAuthed] = useState(hasToken());

  useEffect(() => {
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener("da:unauthorized", onUnauthorized);
    return () => window.removeEventListener("da:unauthorized", onUnauthorized);
  }, []);

  return authed ? <Chat /> : <Login onLogin={() => setAuthed(true)} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

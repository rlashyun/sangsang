import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AgentSearchApp } from "./components/AgentSearchApp";
import { AlertConfirm } from "./components/AlertConfirm";

export const agentEnabled = document.getElementById("root")?.dataset.agentSearch === "enabled"
  || (import.meta.env.DEV && new URLSearchParams(window.location.search).get("search") === "chat");

export const router = createBrowserRouter([
  { path: "/alerts/confirm", element: <AlertConfirm /> },
  { path: "/alerts/extend", element: <AgentSearchApp /> },
  { path: "/alerts/cancel", element: <AgentSearchApp /> },
  // PRD 4.3-E(2026-11-24) — 결과 메일의 "리트리버에서 보기"
  { path: "/alerts/results", element: <AgentSearchApp /> },
  {
    path: "/",
    element: agentEnabled ? <AgentSearchApp /> : <App />,
  },
]);

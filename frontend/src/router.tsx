import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AgentSearchApp } from "./components/AgentSearchApp";
import { AlertConfirm } from "./components/AlertConfirm";

export const agentEnabled = document.getElementById("root")?.dataset.agentSearch === "enabled"
  || (import.meta.env.DEV && new URLSearchParams(window.location.search).get("search") === "chat");

export const router = createBrowserRouter([
  { path: "/alerts/confirm", element: <AlertConfirm /> },
  {
    path: "/",
    element: agentEnabled ? <AgentSearchApp /> : <App />,
  },
]);

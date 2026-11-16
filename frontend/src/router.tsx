import { createBrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AgentSearchApp } from "./components/AgentSearchApp";

export const agentEnabled = document.getElementById("root")?.dataset.agentSearch === "enabled"
  || (import.meta.env.DEV && new URLSearchParams(window.location.search).get("search") === "chat");

export const router = createBrowserRouter([
  {
    path: "/",
    element: agentEnabled ? <AgentSearchApp /> : <App />,
  },
]);

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { agentEnabled, router } from "./router";
import { ScreenComparison } from "./components/ScreenComparison";
import { applyPreviewTheme } from "./lib/theme";
import "./styles.css";

// PRD 4.4 — 지도 핀과 반경이 토큰을 읽기 전에 기본 테마를 적용한다.
if (agentEnabled) applyPreviewTheme("retriever-blue");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {agentEnabled ? <RouterProvider router={router} /> : <ScreenComparison><RouterProvider router={router} /></ScreenComparison>}
  </StrictMode>,
);

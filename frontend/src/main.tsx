import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { router } from "./router";
import { ScreenComparison } from "./components/ScreenComparison";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ScreenComparison><RouterProvider router={router} /></ScreenComparison>
  </StrictMode>,
);

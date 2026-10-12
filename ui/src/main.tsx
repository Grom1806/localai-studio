import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { StatusProvider, ToastProvider } from "./components/common";
import "./styles.css";
import { startI18n } from "./i18n";

startI18n();

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <StatusProvider>
        <App />
      </StatusProvider>
    </ToastProvider>
  </React.StrictMode>,
);

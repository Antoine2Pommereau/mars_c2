import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ConnexionDemandee from "./components/ConnexionDemandee";
import "./styles.css";
import { initialiserTheme } from "./lib/theme";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

initialiserTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <ConnexionDemandee />
    </QueryClientProvider>
  </StrictMode>,
);

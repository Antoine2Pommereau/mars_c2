import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import ConnexionDemandee from "./components/ConnexionDemandee";
import "./styles.css";
import { initialiserTheme } from "./lib/theme";

// Deux pages : la Veille et la fiche de signalement imprimable, chacune chargée seulement quand on l'ouvre
const App = lazy(() => import("./App"));
const Signalement = lazy(() => import("./components/Signalement"));

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

initialiserTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={null}>
        {window.location.pathname === "/signalement" ? <Signalement /> : <><App /><ConnexionDemandee /></>}
      </Suspense>
    </QueryClientProvider>
  </StrictMode>,
);

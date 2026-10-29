// Fiche de signalement en PDF (lot UX2), en mode démonstration par défaut (noms, numéros et photos masqués par l'API).
// Usage : node pdf.mjs <adresse de l'interface> <clé du dossier> <instant ISO> <sortie sans extension> [demo|reel]
// Exemple : node pdf.mjs http://localhost:8080 navire:2458 2026-10-25T18:30:00Z fiche demo ; Chromium de Playwright.
import { chromium } from "playwright";
const [, , base, cle, instant, sortie, demo = "demo"] = process.argv;
const exe = process.env.CHROMIUM || undefined;          // Chromium de Playwright par défaut
const b = await chromium.launch({ executablePath: exe, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const ctx = await b.newContext({ viewport: { width: 1100, height: 1400 } });
const p = await ctx.newPage();
const erreurs = [];
p.on("pageerror", (e) => erreurs.push(String(e)));
p.on("console", (m) => { if (m.type() === "error" && !/otel/.test(m.text())) erreurs.push(m.text()); });
await p.goto(base + "/"); await p.waitForTimeout(1500);
if (demo === "demo") await p.evaluate(() => fetch("/api/controle/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ masquage: true }) }));
await p.goto(`${base}/signalement?cle=${encodeURIComponent(cle)}&instant=${encodeURIComponent(instant)}`);
await p.waitForSelector("[data-pret]", { timeout: 90000 }).catch(() => erreurs.push("carte statique jamais prête"));
await p.waitForTimeout(2500);
console.log("titre :", await p.title());
await p.screenshot({ path: `${sortie}.png`, fullPage: true });
await p.pdf({ path: `${sortie}.pdf`, format: "A4", printBackground: true });
console.log("erreurs :", JSON.stringify(erreurs.slice(0, 6)));
await b.close();

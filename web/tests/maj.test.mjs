// Mise à jour du serveur (lot UX1) : bascule après deux pages de mise à jour de suite, jamais sur une erreur de l'API.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pageDeMaj, signalerReponse } from "../src/lib/maj.ts";

test("la page de nginx (HTML) seulement, deux fois de suite", () => {
  assert.equal(pageDeMaj(503, "text/html"), true);
  assert.equal(pageDeMaj(503, "application/json"), false);       // refus de l'API : jamais une mise à jour
  assert.equal(pageDeMaj(500, "text/html"), false);
  assert.equal(signalerReponse(503, "text/html; charset=utf-8"), false);
  assert.equal(signalerReponse(200, "application/json"), false);  // une réponse saine remet le compte à zéro
  assert.equal(signalerReponse(503, "text/html"), false);
  assert.equal(signalerReponse(502, "text/html"), true);
});

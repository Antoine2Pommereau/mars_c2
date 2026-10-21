// État dégradé (lot U, étape 6) : la Veille se dit aveugle en direct seulement, au delà de 15 minutes sans AIS.
import assert from "node:assert/strict";
import { test } from "node:test";
import { aveugle } from "../src/lib/systeme.ts";

const NOW = Date.parse("2026-10-09T21:00:00Z");

test("au delà de 15 minutes sans position, en direct : aveugle", () => {
  assert.equal(aveugle("2026-10-09T20:48:00Z", NOW, true), null, "12 minutes : la veille voit encore");
  const a = aveugle("2026-10-09T20:40:00Z", NOW, true);
  assert.equal(Math.round(a.minutes), 20);
});

test("en plage ou en rejeu, jamais : l'écran montre le passé", () => {
  assert.equal(aveugle("2026-10-01T00:00:00Z", NOW, false), null);
});

test("aucune position connue : aveugle sans heure", () => {
  assert.deepEqual(aveugle(null, NOW, true), { minutes: Infinity, derniere: null });
});

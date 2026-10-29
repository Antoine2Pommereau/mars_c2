// Glossaire (lot UX1) : chaque terme a sa phrase, une seule source, sans tiret.
import assert from "node:assert/strict";
import { test } from "node:test";
import { L } from "../src/lib/libelles.ts";

test("sept termes, une phrase chacun, aucun tiret", () => {
  assert.deepEqual(Object.keys(L.glossaire).sort(),
    ["AIS", "MMSI", "OMI", "SAR", "VIIRS", "tip and cue", "zone de réception fiable"].sort());
  for (const [t, p] of Object.entries(L.glossaire)) {
    assert.match(p, /^[A-ZÉ].+[.]$/, t);
    assert.doesNotMatch(p, /[-‐‑‒–—]/, `${t} : tiret`);
  }
});

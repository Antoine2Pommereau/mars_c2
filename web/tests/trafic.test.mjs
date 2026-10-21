// Décodage du trafic pour la carte (lot A, 21/10/2026) : les deux formes en colonnes doivent donner les mêmes navires.
// En mode plage et rejeu, /api/traffic renvoie {at, cols, rows} ; le flux en direct, {navires: {cols, rows}}. Seule la
// seconde était acceptée : la carte restait vide hors du direct.
//     npm test   (node --test, sans dépendance)
import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeTraffic } from "../src/lib/trafic.ts";

const COLS = ["vessel_id", "lon", "lat", "sog_kn", "cog_deg", "t", "mmsi", "name", "ship_type", "length_m", "flag", "watch"];
const ROWS = [[1626, -1.09715, 50.81096, 0.0, 162, 1791579265, 227568414, "YOLE 372", "Passenger", 165, "FR", null],
              [6140, 0.21, 50.3, 11.2, 245, 1791579000, 230538622, "GOELAND 466", "Tanker", 150, "FI", "sanctionne"]];
const INSTANT = 1791579600 * 1000;

test("route /api/traffic des modes plage et rejeu : {at, cols, rows}", () => {
  const fc = decodeTraffic({ at: "2026-10-09T21:00:00+00:00", cols: COLS, rows: ROWS }, INSTANT);
  assert.equal(fc.features.length, 2);
  assert.deepEqual(fc.features[1].geometry.coordinates, [0.21, 50.3]);
  assert.equal(fc.features[1].properties.name, "GOELAND 466");
  assert.equal(fc.features[0].properties.age_s, 335);
});

test("flux en direct : {navires: {cols, rows}}, même résultat", () => {
  const a = decodeTraffic({ navires: { cols: COLS, rows: ROWS } }, INSTANT);
  const b = decodeTraffic({ at: "x", cols: COLS, rows: ROWS }, INSTANT);
  assert.deepEqual(a, b);
});

test("ancienne forme GeoJSON et réponse vide", () => {
  const fc = { type: "FeatureCollection", features: [] };
  assert.equal(decodeTraffic({ traffic: fc }, INSTANT), fc);
  assert.equal(decodeTraffic(undefined, INSTANT), null);
  assert.equal(decodeTraffic({ clock: {} }, INSTANT), null);
});

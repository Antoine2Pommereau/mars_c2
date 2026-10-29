// Fiche de signalement (lot UX2) : adresse d'un dossier, ouverte depuis le récit (bouton ou touche P).
import assert from "node:assert/strict";
import { test } from "node:test";
import { adresseSignalement } from "../src/lib/signalement.ts";

test("clé, instant et rejeu tel qu'il était encodés dans l'adresse", () => {
  assert.equal(adresseSignalement("navire:2458", "2026-10-25T18:30:00.000Z"),
    "/signalement?cle=navire%3A2458&instant=2026-10-25T18%3A30%3A00.000Z");
  assert.ok(adresseSignalement("point:2987", "t", "2026-10-25T18:30:00Z").endsWith("&a_l_instant=2026-10-25T18%3A30%3A00Z"));
});

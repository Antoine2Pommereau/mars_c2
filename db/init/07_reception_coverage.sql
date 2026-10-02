-- MARS C2, phase 4 : qualité de réception mesurée par la continuité des trajectoires. Idempotent.
ALTER TABLE reception_cells ADD COLUMN IF NOT EXISTS coverage REAL;

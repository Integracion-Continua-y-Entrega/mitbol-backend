-- HU-007: SQL para revisión; NO aplicado en mitbol_dev.
-- Conserva is_available; false impide vender hasta una sincronización autorizada.
BEGIN;

-- Running upgrade 20261102_0002 -> 20261102_0003

ALTER TABLE "ProductVariants" ADD COLUMN is_commercially_available BOOLEAN DEFAULT false NOT NULL;

UPDATE alembic_version SET version_num='20261102_0003' WHERE alembic_version.version_num = '20261102_0002';

COMMIT;


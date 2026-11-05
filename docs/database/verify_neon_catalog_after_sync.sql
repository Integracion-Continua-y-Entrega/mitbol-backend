-- Post-sync, solo lectura. Manifiesto PUBLICO: JSON UTF-8 transportado en Base64 ASCII.
\set ON_ERROR_STOP on
\encoding UTF8
\pset pager off
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL search_path = pg_catalog, public;
-- Informacion de la conexion REAL de este psql, no del salto proxy -> backend.
-- Bloque C fija LC_ALL=C y valida este CSV con libpq verify-full/channel_binding=require.
\pset format csv
\pset footer off
\echo MITBOL_CLIENT_CONNINFO_BEGIN
\conninfo
\echo MITBOL_CLIENT_CONNINFO_END
\pset format aligned
\pset footer on
\getenv approved_manifest_b64 MITBOL_SYNC_APPROVED_MANIFEST_B64
\if :{?approved_manifest_b64}
\else
    \echo 'Falta MITBOL_SYNC_APPROVED_MANIFEST_B64; detenerse.'
    DO $failure$ BEGIN RAISE EXCEPTION 'MISSING_APPROVED_MANIFEST'; END $failure$;
\endif
\if :{?reader_mode}
\else
    \set reader_mode false
\endif

SELECT current_database() AS database, current_user AS username,
       session_user, current_setting('transaction_read_only') AS transaction_read_only,
       current_setting('default_transaction_read_only') AS default_transaction_read_only,
       ssl AS backend_ssl_observed, version AS backend_tls_version_observed
FROM pg_stat_ssl WHERE pid = pg_backend_pid();
SELECT current_database() = 'mitbol_cloud_dev'
   AND current_user = session_user
   AND current_user = CASE WHEN :'reader_mode'::boolean THEN 'mitbol_render_reader' ELSE 'neondb_owner' END
   AND current_setting('transaction_read_only') = 'on'
   AND (NOT :'reader_mode'::boolean OR current_setting('default_transaction_read_only') = 'on')
   AS identity_ok \gset
\if :identity_ok
\else
    \echo 'Base, usuario, session_user o modo de lectura incorrectos.'
    DO $failure$ BEGIN RAISE EXCEPTION 'POST_SYNC_IDENTITY'; END $failure$;
\endif

\if :reader_mode
    \echo 'Login lector: Alembic se verifica por separado con el operador; no se amplian permisos.'
\else
    SELECT array_agg(version_num ORDER BY version_num) = ARRAY['20261102_0003']::varchar[]
       AS revision_ok FROM public.alembic_version \gset
    \if :revision_ok
        SELECT version_num AS alembic_revision FROM public.alembic_version;
    \else
        \echo 'Revision Alembic incorrecta.'
        DO $failure$ BEGIN RAISE EXCEPTION 'POST_SYNC_REVISION'; END $failure$;
    \endif
\endif

SELECT 'Products' AS table_name, count(*) AS rows FROM public."Products"
UNION ALL SELECT 'ProductVariants', count(*) FROM public."ProductVariants"
UNION ALL SELECT 'ProductModifiers', count(*) FROM public."ProductModifiers"
UNION ALL SELECT 'ProductComplements', count(*) FROM public."ProductComplements"
UNION ALL SELECT 'ProductModifierAssignments', count(*) FROM public."ProductModifierAssignments"
UNION ALL SELECT 'ProductImages', count(*) FROM public."ProductImages"
UNION ALL SELECT 'Tags', count(*) FROM public."Tags"
UNION ALL SELECT 'ProductTags', count(*) FROM public."ProductTags" ORDER BY 1;

-- Sustitucion psql en SQL de primer nivel, nunca dentro de DO/$$ ni otro literal.
WITH approved AS (
    SELECT convert_from(decode(:'approved_manifest_b64', 'base64'), 'UTF8')::jsonb AS d
),
ep AS (SELECT x.* FROM approved, jsonb_to_recordset(d->'products') x(loyverse_id text, name text)),
ev AS (SELECT x.* FROM approved, jsonb_to_recordset(d->'variants') x(
    loyverse_id text, product_loyverse_id text, name text, price numeric, is_commercially_available boolean)),
em AS (SELECT x.* FROM approved, jsonb_to_recordset(d->'modifiers') x(loyverse_id text, name text, display_order integer)),
ec AS (SELECT x.* FROM approved, jsonb_to_recordset(d->'complements') x(
    loyverse_id text, modifier_loyverse_id text, name text, price numeric, display_order integer)),
ea AS (SELECT x->>0 AS variant_id, x->>1 AS modifier_id FROM approved, jsonb_array_elements(d->'assignments') x),
duplicates AS (
    SELECT loyverse_id FROM public."Products" GROUP BY loyverse_id HAVING count(*) > 1
    UNION ALL SELECT loyverse_id FROM public."ProductVariants" GROUP BY loyverse_id HAVING count(*) > 1
    UNION ALL SELECT loyverse_id FROM public."ProductModifiers" GROUP BY loyverse_id HAVING count(*) > 1
    UNION ALL SELECT loyverse_id FROM public."ProductComplements" GROUP BY loyverse_id HAVING count(*) > 1
), orphans AS (
    SELECT v.product_variant_id FROM public."ProductVariants" v LEFT JOIN public."Products" p USING(product_id) WHERE p.product_id IS NULL
    UNION ALL SELECT c.product_complement_id FROM public."ProductComplements" c LEFT JOIN public."ProductModifiers" m USING(product_modifier_id) WHERE m.product_modifier_id IS NULL
    UNION ALL SELECT a.product_variant_id FROM public."ProductModifierAssignments" a LEFT JOIN public."ProductVariants" v USING(product_variant_id) WHERE v.product_variant_id IS NULL
    UNION ALL SELECT a.product_modifier_id FROM public."ProductModifierAssignments" a LEFT JOIN public."ProductModifiers" m USING(product_modifier_id) WHERE m.product_modifier_id IS NULL
    UNION ALL SELECT i.product_image_id FROM public."ProductImages" i LEFT JOIN public."ProductVariants" v USING(product_variant_id) WHERE v.product_variant_id IS NULL
    UNION ALL SELECT t.product_variant_id FROM public."ProductTags" t LEFT JOIN public."ProductVariants" v USING(product_variant_id) WHERE v.product_variant_id IS NULL
    UNION ALL SELECT t.tag_id FROM public."ProductTags" t LEFT JOIN public."Tags" g USING(tag_id) WHERE g.tag_id IS NULL
)
SELECT
    (SELECT count(*) FROM duplicates) AS duplicate_ids,
    (SELECT count(*) FROM orphans) AS orphan_relationships,
    (SELECT count(*) FROM public."ProductVariants" WHERE is_commercially_available) AS available_variants,
    (SELECT count(*) FROM approved, jsonb_array_elements_text(d->'deleted_products') x
       JOIN public."Products" p ON p.loyverse_id = x.value) AS tombstones_imported,
    (
        (SELECT d->'store'->>'id' = '9e30730f-a5b4-4485-8cc4-bc499cc6e239' AND d->'store'->>'name' = 'I.C.E' FROM approved)
        AND (SELECT count(*) FROM ep) = 3 AND (SELECT count(*) FROM ev) = 5
        AND (SELECT count(*) FROM em) = 2 AND (SELECT count(*) FROM ec) = 5 AND (SELECT count(*) FROM ea) = 4
        AND (SELECT count(*) FROM public."Products") = 3 AND (SELECT count(*) FROM public."ProductVariants") = 5
        AND (SELECT count(*) FROM public."ProductModifiers") = 2 AND (SELECT count(*) FROM public."ProductComplements") = 5
        AND (SELECT count(*) FROM public."ProductModifierAssignments") = 4
        AND NOT EXISTS (SELECT 1 FROM public."ProductImages") AND NOT EXISTS (SELECT 1 FROM public."Tags")
        AND NOT EXISTS (SELECT 1 FROM public."ProductTags")
        AND NOT EXISTS (SELECT 1 FROM duplicates) AND NOT EXISTS (SELECT 1 FROM orphans)
        AND (SELECT count(*) FROM public."ProductVariants" WHERE is_commercially_available) = 5
        AND NOT EXISTS (SELECT 1 FROM approved, jsonb_array_elements_text(d->'deleted_products') x
                        JOIN public."Products" p ON p.loyverse_id = x.value)
        AND NOT EXISTS (SELECT 1 FROM ep e LEFT JOIN public."Products" p USING(loyverse_id)
            WHERE p.product_id IS NULL OR p.name IS DISTINCT FROM e.name OR NOT p.is_active OR NOT p.is_available
               OR p.description IS NOT NULL OR p.is_featured OR NOT p.is_new)
        AND NOT EXISTS (SELECT 1 FROM ev e LEFT JOIN public."ProductVariants" v USING(loyverse_id)
            LEFT JOIN public."Products" p USING(product_id)
            WHERE v.product_variant_id IS NULL OR v.name IS DISTINCT FROM e.name OR v.price IS DISTINCT FROM e.price
               OR p.loyverse_id IS DISTINCT FROM e.product_loyverse_id OR NOT v.is_available
               OR v.is_commercially_available IS DISTINCT FROM e.is_commercially_available)
        AND NOT EXISTS (SELECT 1 FROM em e LEFT JOIN public."ProductModifiers" m USING(loyverse_id)
            WHERE m.product_modifier_id IS NULL OR m.name IS DISTINCT FROM e.name OR m.display_order IS DISTINCT FROM e.display_order)
        AND NOT EXISTS (SELECT 1 FROM ec e LEFT JOIN public."ProductComplements" c USING(loyverse_id)
            LEFT JOIN public."ProductModifiers" m USING(product_modifier_id)
            WHERE c.product_complement_id IS NULL OR c.name IS DISTINCT FROM e.name OR c.price IS DISTINCT FROM e.price
               OR m.loyverse_id IS DISTINCT FROM e.modifier_loyverse_id OR c.display_order IS DISTINCT FROM e.display_order OR NOT c.is_active)
        AND NOT EXISTS (SELECT 1 FROM ea e WHERE NOT EXISTS (
            SELECT 1 FROM public."ProductModifierAssignments" a JOIN public."ProductVariants" v USING(product_variant_id)
            JOIN public."ProductModifiers" m USING(product_modifier_id)
            WHERE v.loyverse_id = e.variant_id AND m.loyverse_id = e.modifier_id))
    ) AS catalog_ok
\gset check_

\echo 'duplicate_ids=' :check_duplicate_ids 'orphan_relationships=' :check_orphan_relationships 'available_variants=' :check_available_variants 'tombstones_imported=' :check_tombstones_imported
SELECT p.loyverse_id AS product_loyverse_id, p.name AS product_name,
       v.loyverse_id AS variant_loyverse_id, v.name AS variant_name, v.price,
       p.is_active, p.is_available AS product_local_available,
       v.is_available AS variant_local_available, v.is_commercially_available
FROM public."Products" p JOIN public."ProductVariants" v USING(product_id)
ORDER BY p.loyverse_id, v.loyverse_id;
\if :check_catalog_ok
    -- Este resultado solo certifica los datos; Bloque C agrega la validacion TLS del cliente.
    SELECT 'catalog_data_verified_after_sync' AS status, current_user AS verified_login;
    ROLLBACK;
\else
    \echo 'CATALOG_MISMATCH: detenerse; no repetir sincronizacion.'
    DO $failure$ BEGIN RAISE EXCEPTION 'CATALOG_MISMATCH'; END $failure$;
\endif

-- PROPUESTA: verificador READ ONLY del trabajador; LOGIN real, sin SET ROLE.
-- Neon requiere validación externa de destino/branch/TLS antes de esta sesión.
-- FASE 4: verificación de ACL, sólo lectura, desde un LOGIN independiente.
-- No usa SET ROLE ni asume que el nombre de rol prueba sus privilegios.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';

DO $policy$
DECLARE
    worker oid;
    rel record;
    col record;
    operation text;
    expected boolean;
    allowed jsonb;
    categories_installed boolean := to_regclass('public."Categories"') IS NOT NULL;
    category_grants boolean := false;
    policy jsonb := '{
      "Products": {"SELECT":["product_id","loyverse_id","name","description","is_available","is_active","is_featured","is_new","synced_at"],"INSERT":["loyverse_id","name","is_active","synced_at"],"UPDATE":["name","is_active","synced_at"]},
      "ProductVariants": {"SELECT":["product_variant_id","loyverse_id","name","price","is_available","is_commercially_available","synced_at","product_id"],"INSERT":["loyverse_id","name","price","product_id","is_available","is_commercially_available","synced_at"],"UPDATE":["name","price","is_commercially_available","synced_at"]},
      "ProductModifiers": {"SELECT":["product_modifier_id","loyverse_id","name","display_order","synced_at"],"INSERT":["loyverse_id","name","display_order","synced_at"],"UPDATE":["name","display_order","synced_at"]},
      "ProductComplements": {"SELECT":["product_complement_id","product_modifier_id","loyverse_id","name","price","is_active","synced_at","display_order"],"INSERT":["loyverse_id","name","price","display_order","product_modifier_id","is_active","synced_at"],"UPDATE":["name","price","display_order","is_active","synced_at"]},
      "ProductModifierAssignments": {"SELECT":["product_variant_id","product_modifier_id"],"INSERT":["product_variant_id","product_modifier_id"],"DELETE":true},
      "ProductImages": {"SELECT":["product_image_id","product_variant_id","image_url","display_order","created_at"]},
      "Tags": {"SELECT":["tag_id","name","created_at"]},
      "ProductTags": {"SELECT":["product_variant_id","tag_id"]},
      "alembic_version": {"SELECT":["version_num"]},
      "SyncRuns": {"SELECT":["run_id","catalog_key","store_id","source","status","requested_by","created_at","started_at","finished_at","available_at","attempts","attempt_token","result","error_code"],"INSERT":["run_id","store_id","source","requested_by"],"UPDATE":["status","started_at","finished_at","available_at","attempts","attempt_token","result","error_code"]}
    }';
BEGIN
    IF current_user <> 'mitbol_catalog_worker' OR session_user <> 'mitbol_catalog_worker'
       OR current_setting('server_version_num')::int / 10000 <> 18
       OR NOT (
           (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
            AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false))
           OR (current_database()='mitbol_cloud_dev'
               AND coalesce(current_setting('mitbol.external_tls_verified', true),'')='verify-full-channel-binding')
       ) THEN
        RAISE EXCEPTION 'WORKER_IDENTITY';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE ((NOT categories_installed AND version_num='20261114_0007') OR (categories_installed AND version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016','20261124_0017','20261125_0018')))) THEN
        RAISE EXCEPTION 'WORKER_SCHEMA';
    END IF;
    SELECT oid INTO worker FROM pg_roles WHERE rolname = current_user;
    IF categories_installed THEN
        IF NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014','20261124_0015','20261124_0016','20261124_0017','20261125_0018')) THEN
            RAISE EXCEPTION 'WORKER_CATEGORIES_SCHEMA';
        END IF;
        category_grants := has_any_column_privilege(worker,'public."Categories"','SELECT,INSERT,UPDATE')
            OR has_sequence_privilege(worker,'public."Categories_category_id_seq"','USAGE');
        -- SELECT de Products a nivel tabla alcanza automáticamente category_id.
        policy := jsonb_set(policy,'{Products,SELECT}',(policy->'Products'->'SELECT') || '["category_id"]'::jsonb);
        IF category_grants THEN
            policy := policy || '{"Categories":{"SELECT":["category_id","loyverse_id","name","color","is_active","synced_at"],"INSERT":["loyverse_id","name","color","is_active","synced_at"],"UPDATE":["name","color","is_active","synced_at"]}}'::jsonb;
            policy := jsonb_set(policy,'{Products,INSERT}',(policy->'Products'->'INSERT') || '["category_id"]'::jsonb);
            policy := jsonb_set(policy,'{Products,UPDATE}',(policy->'Products'->'UPDATE') || '["category_id"]'::jsonb);
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = worker AND
       (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = worker) THEN
        RAISE EXCEPTION 'WORKER_ATTRIBUTES_OR_MEMBERSHIP';
    END IF;
    IF NOT has_database_privilege(worker, current_database(), 'CONNECT')
       OR NOT has_schema_privilege(worker, 'public', 'USAGE')
       OR has_database_privilege(worker, current_database(), 'CREATE,TEMPORARY')
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE has_schema_privilege(worker, oid, 'CREATE')) THEN
        RAISE EXCEPTION 'WORKER_DATABASE_OR_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_database WHERE datdba = worker)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = worker)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = worker)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = worker)
       OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = worker)
       OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = worker)
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata WHERE lomowner = worker) THEN
        RAISE EXCEPTION 'WORKER_OWNER';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND policy ? c.relname::text) <> (CASE WHEN category_grants THEN 11 ELSE 10 END) THEN
        RAISE EXCEPTION 'WORKER_MISSING_RELATIONS';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S' AND c.relname IN
        ('Products_product_id_seq','ProductVariants_product_variant_id_seq','ProductModifiers_product_modifier_id_seq','ProductComplements_product_complement_id_seq','Categories_category_id_seq')) <> (CASE WHEN categories_installed THEN 5 ELSE 4 END) THEN
        RAISE EXCEPTION 'WORKER_MISSING_SEQUENCES';
    END IF;
    FOR rel IN SELECT c.*, n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
    LOOP
        allowed := CASE WHEN rel.nspname = 'public' AND rel.relkind = 'r' THEN policy -> rel.relname::text ELSE NULL END;
        IF rel.relkind IN ('r','p','v','m','f') THEN
            IF allowed IS NOT NULL AND (rel.relrowsecurity OR NOT has_table_privilege(worker,rel.oid,'SELECT')) THEN
                RAISE EXCEPTION 'WORKER_READ_PROFILE';
            END IF;
            FOR col IN SELECT attnum, attname FROM pg_attribute
                WHERE attrelid = rel.oid AND attnum > 0 AND NOT attisdropped
            LOOP
                FOREACH operation IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
                    expected := coalesce((allowed -> operation) ? col.attname::text, false);
                    IF has_column_privilege(worker, rel.oid, col.attnum, operation) <> expected THEN
                        RAISE EXCEPTION 'WORKER_COLUMN: %.% %', rel.relname, col.attname, operation;
                    END IF;
                END LOOP;
            END LOOP;
            IF has_table_privilege(worker, rel.oid, 'DELETE') <> coalesce((allowed ->> 'DELETE')::boolean, false)
               OR has_table_privilege(worker, rel.oid, 'TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') THEN
                RAISE EXCEPTION 'WORKER_TABLE: %', rel.relname;
            END IF;
        ELSIF rel.relkind = 'S' THEN
            expected := rel.nspname = 'public' AND rel.relname IN
                ('Products_product_id_seq','ProductVariants_product_variant_id_seq','ProductModifiers_product_modifier_id_seq','ProductComplements_product_complement_id_seq');
            expected := expected OR (category_grants AND rel.nspname='public' AND rel.relname='Categories_category_id_seq');
            IF has_sequence_privilege(worker, rel.oid, 'USAGE') <> expected
               OR has_sequence_privilege(worker, rel.oid, 'SELECT,UPDATE') THEN
                RAISE EXCEPTION 'WORKER_SEQUENCE: %', rel.relname;
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM (
        SELECT relacl AS acl FROM pg_class UNION ALL SELECT attacl FROM pg_attribute
        UNION ALL SELECT nspacl FROM pg_namespace UNION ALL SELECT datacl FROM pg_database
        UNION ALL SELECT proacl FROM pg_proc
    ) objects CROSS JOIN LATERAL aclexplode(objects.acl) a
        WHERE a.grantee IN (worker, 0) AND a.is_grantable) THEN
        RAISE EXCEPTION 'WORKER_GRANT_OPTION';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE a.grantee IN (worker, 0) AND
        (d.defaclobjtype IN ('r','S','L') OR (d.defaclobjtype = 'n' AND a.privilege_type = 'CREATE') OR a.is_grantable)) THEN
        RAISE EXCEPTION 'WORKER_UNSAFE_DEFAULTS';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
          AND has_schema_privilege(worker, n.oid, 'USAGE') AND has_function_privilege(worker, p.oid, 'EXECUTE')) THEN
        RAISE EXCEPTION 'WORKER_UNREVIEWED_ROUTINES';
    END IF;
END
$policy$;
SELECT current_database() AS database, current_user AS effective_user, session_user AS login_user,
       'WORKER_POLICY_OK_REVIEWED_PROFILE' AS result;
COMMIT;

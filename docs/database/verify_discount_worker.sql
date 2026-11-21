-- SOLO LECTURA. LOGIN real del trabajador; no sustituir por SET ROLE.
-- El cliente verifica externamente host directo y TLS antes de fijar la marca.
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
    policy jsonb := '{
      "alembic_version":{"SELECT":["version_num"]},
      "Discounts":{
        "SELECT":["discount_id","loyverse_id","name","discount_type","value","scope","is_active","synced_at","created_at"],
        "INSERT":["loyverse_id","name","discount_type","value","scope","is_active","synced_at"],
        "UPDATE":["name","discount_type","value","scope","is_active","synced_at"]
      }
    }';
BEGIN
    IF current_user <> 'mitbol_discount_worker' OR session_user <> current_user
       OR current_setting('server_version_num')::int / 10000 <> 18
       OR NOT coalesce(
           (current_database() ~ '^mitbol_test_(auth_[a-f0-9]{32}|hu008_[a-f0-9]{12})$'
            AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false))
           OR (current_database() = 'mitbol_cloud_dev'
               AND current_setting('neon.project_id', true) = 'delicate-glade-05265146'
               AND current_setting('neon.branch_id', true) = 'br-snowy-king-bd0n9w42'
               AND current_setting('neon.endpoint_id', true) = 'ep-green-hill-bddfbmgh'
               AND coalesce(current_setting('mitbol.external_tls_verified', true),'') = 'verify-full-channel-binding'), false
       ) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_IDENTITY';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012','20261120_0013','20261121_0014')) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_SCHEMA';
    END IF;
    SELECT oid INTO worker FROM pg_roles WHERE rolname = current_user;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = worker AND
        (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = worker) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_ATTRIBUTES_OR_MEMBERSHIP';
    END IF;
    IF NOT has_database_privilege(worker,current_database(),'CONNECT')
       OR has_database_privilege(worker,current_database(),'CREATE,TEMPORARY')
       OR NOT has_schema_privilege(worker,'public','USAGE')
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE has_schema_privilege(worker,oid,'CREATE'))
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname <> 'public'
            AND nspname <> 'information_schema' AND nspname !~ '^pg_'
            AND has_schema_privilege(worker,oid,'USAGE')) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_DATABASE_OR_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_database WHERE datdba = worker)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = worker)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = worker)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = worker)
       OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = worker)
       OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = worker)
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata WHERE lomowner = worker) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_OWNER';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND policy ? c.relname::text) <> 2
       OR pg_get_serial_sequence('public."Discounts"','discount_id')
            IS DISTINCT FROM 'public."Discounts_discount_id_seq"' THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_MISSING_RELATIONS';
    END IF;
    FOR rel IN SELECT c.*,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
    LOOP
        allowed := CASE WHEN rel.nspname = 'public' AND rel.relkind = 'r' THEN policy -> rel.relname::text ELSE NULL END;
        IF rel.relkind IN ('r','p','v','m','f') THEN
            IF allowed IS NOT NULL AND (rel.relrowsecurity OR rel.relforcerowsecurity) THEN
                RAISE EXCEPTION 'DISCOUNT_WORKER_RLS';
            END IF;
            FOR col IN SELECT attnum,attname FROM pg_attribute
                WHERE attrelid = rel.oid AND attnum > 0 AND NOT attisdropped
            LOOP
                FOREACH operation IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
                    expected := coalesce((allowed -> operation) ? col.attname::text,false);
                    IF has_column_privilege(worker,rel.oid,col.attnum,operation) <> expected THEN
                        RAISE EXCEPTION 'DISCOUNT_WORKER_COLUMN: %.% %',rel.relname,col.attname,operation;
                    END IF;
                END LOOP;
            END LOOP;
            IF has_table_privilege(worker,rel.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') THEN
                RAISE EXCEPTION 'DISCOUNT_WORKER_TABLE_GRANT: %',rel.relname;
            END IF;
        ELSIF rel.relkind = 'S' THEN
            expected := rel.nspname = 'public' AND rel.relname = 'Discounts_discount_id_seq';
            IF has_sequence_privilege(worker,rel.oid,'USAGE') <> expected
               OR has_sequence_privilege(worker,rel.oid,'SELECT,UPDATE') THEN
                RAISE EXCEPTION 'DISCOUNT_WORKER_SEQUENCE: %',rel.relname;
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM (
        SELECT relacl AS acl FROM pg_class UNION ALL SELECT attacl FROM pg_attribute
        UNION ALL SELECT nspacl FROM pg_namespace UNION ALL SELECT datacl FROM pg_database
        UNION ALL SELECT proacl FROM pg_proc
    ) objects CROSS JOIN LATERAL aclexplode(objects.acl) a
        WHERE a.grantee IN (worker,0) AND a.is_grantable) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_GRANT_OPTION';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE a.grantee IN (worker,0) AND (d.defaclobjtype IN ('r','S','L')
            OR (d.defaclobjtype = 'n' AND a.privilege_type = 'CREATE') OR a.is_grantable)) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_UNSAFE_DEFAULTS';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
            AND has_schema_privilege(worker,n.oid,'USAGE') AND has_function_privilege(worker,p.oid,'EXECUTE'))
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata WHERE has_largeobject_privilege(worker,oid,'SELECT,UPDATE')) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_UNREVIEWED_OBJECTS';
    END IF;
END
$policy$;
SELECT current_database() AS database,current_user AS effective_user,session_user AS login_user,
       'DISCOUNT_WORKER_POLICY_OK' AS result;
COMMIT;

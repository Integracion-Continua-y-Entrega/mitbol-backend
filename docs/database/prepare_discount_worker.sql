-- PROPUESTA HU-009: no ejecutar sin aprobación específica y conexión contrastada.
-- Sin secretos. No crea contraseñas ni modifica roles existentes o PUBLIC.
-- Requiere mitbol.approved_discount_worker_write=hu009-discount-worker-reviewed.
-- Neon: el cliente debe verificar host directo, branch, base y TLS efectivo;
-- la marca mitbol.external_tls_verified NO sustituye esa comprobación.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
BEGIN
    IF coalesce(current_setting('mitbol.approved_discount_worker_write', true), '')
            <> 'hu009-discount-worker-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_NOT_APPROVED';
    END IF;
    IF NOT coalesce(
        (current_database() ~ '^mitbol_test_(auth_[a-f0-9]{32}|hu008_[a-f0-9]{12})$'
         AND current_user = 'mitbol_ci' AND session_user = 'mitbol_ci'
         AND coalesce(inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet), false))
        OR
        (current_database() = 'mitbol_cloud_dev'
         AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
         AND current_setting('neon.project_id', true) = 'delicate-glade-05265146'
         AND current_setting('neon.branch_id', true) = 'br-snowy-king-bd0n9w42'
         AND current_setting('neon.endpoint_id', true) = 'ep-green-hill-bddfbmgh'
         AND coalesce(current_setting('mitbol.external_tls_verified', true), '') = 'verify-full-channel-binding')
    , false) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012')) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_WRONG_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mitbol_discount_worker') THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_EXISTS: revisar sin sobrescribir.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = to_regclass('public."Discounts"')
        AND relkind = 'r' AND NOT relrowsecurity AND NOT relforcerowsecurity)
       OR pg_get_serial_sequence('public."Discounts"', 'discount_id')
            IS DISTINCT FROM 'public."Discounts_discount_id_seq"'
       OR (SELECT count(*) FROM pg_attribute WHERE attrelid = 'public."Discounts"'::regclass
            AND attnum > 0 AND NOT attisdropped) <> 9
       OR EXISTS (
            SELECT 1 FROM (VALUES
                ('discount_id','integer',true), ('loyverse_id','character varying(100)',true),
                ('name','character varying(100)',true), ('discount_type','character varying(20)',true),
                ('value','numeric',false), ('scope','character varying(20)',true),
                ('is_active','boolean',true), ('synced_at','timestamp with time zone',false),
                ('created_at','timestamp with time zone',true)
            ) wanted(name,kind,required)
            LEFT JOIN pg_attribute a ON a.attrelid = 'public."Discounts"'::regclass
                AND a.attname = wanted.name AND a.attnum > 0 AND NOT a.attisdropped
            WHERE a.attname IS NULL OR format_type(a.atttypid,a.atttypmod) <> wanted.kind
                OR a.attnotnull <> wanted.required
       )
       OR (SELECT count(*) FROM pg_constraint WHERE conrelid = 'public."Discounts"'::regclass
            AND convalidated AND conenforced AND
            ((conname = 'pk_Discounts' AND contype = 'p' AND conkey = ARRAY[1]::smallint[])
             OR (conname = 'uq_Discounts_loyverse_id' AND contype = 'u' AND conkey = ARRAY[2]::smallint[]))) <> 2
       OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'public."Discounts"'::regclass
            AND NOT tgisinternal AND tgenabled <> 'D')
       OR EXISTS (SELECT 1 FROM pg_rewrite WHERE ev_class = 'public."Discounts"'::regclass
            AND rulename <> '_RETURN') THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_DISCOUNTS_SCHEMA';
    END IF;
    -- No intentar corregir PUBLIC automáticamente: podría afectar a otros roles.
    IF EXISTS (SELECT 1 FROM pg_database d CROSS JOIN LATERAL
        aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
        WHERE d.datname = current_database() AND a.grantee = 0
            AND (a.privilege_type <> 'CONNECT' OR a.is_grantable))
       OR EXISTS (SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL
        aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND a.grantee = 0
            AND (n.nspname <> 'public' OR a.privilege_type <> 'USAGE' OR a.is_grantable))
       OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND a.grantee = 0)
       OR EXISTS (SELECT 1 FROM pg_attribute c JOIN pg_class t ON t.oid = c.attrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace CROSS JOIN LATERAL aclexplode(c.attacl) a
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND a.grantee = 0)
       OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND a.grantee = 0)
       OR EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE a.grantee = 0 AND (d.defaclobjtype IN ('r','S','L')
            OR (d.defaclobjtype = 'n' AND a.privilege_type = 'CREATE') OR a.is_grantable))
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata l CROSS JOIN LATERAL aclexplode(l.lomacl) a
        WHERE a.grantee = 0) THEN
        RAISE EXCEPTION 'DISCOUNT_WORKER_UNSAFE_PUBLIC_BASELINE';
    END IF;
END
$guard$;
CREATE ROLE mitbol_discount_worker LOGIN PASSWORD NULL
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
DO $database$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO mitbol_discount_worker', current_database());
END
$database$;
GRANT USAGE ON SCHEMA public TO mitbol_discount_worker;
GRANT SELECT (version_num) ON public.alembic_version TO mitbol_discount_worker;
GRANT SELECT (discount_id,loyverse_id,name,discount_type,value,scope,is_active,synced_at,created_at),
    INSERT (loyverse_id,name,discount_type,value,scope,is_active,synced_at),
    UPDATE (name,discount_type,value,scope,is_active,synced_at)
    ON public."Discounts" TO mitbol_discount_worker;
GRANT USAGE ON SEQUENCE public."Discounts_discount_id_seq" TO mitbol_discount_worker;
ALTER ROLE mitbol_discount_worker SET search_path TO pg_catalog, public;
ALTER ROLE mitbol_discount_worker SET lock_timeout TO '5s';
-- No DELETE, DDL, membresías, grant options, defaults, otras tablas o secuencias.
-- Inicializar contraseña por ALTER ROLE, sin prehash, en otra operación autorizada.
COMMIT;

-- PROPUESTA NO EJECUTADA: LOGIN independiente con TLS verificado. Sólo catálogos.
-- TLS externo obligatorio en el cliente: verify-full, CA y channel_binding=require.
-- La marca de sesión no acredita TLS por sí sola; usar el procedimiento fase 5, sección 7.
-- FASE 4: verificación de ACL, sólo lectura, desde un LOGIN independiente.
-- No usa SET ROLE ni asume que el nombre de rol prueba sus privilegios.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';

DO $policy$
DECLARE
    writer oid;
    rel record;
    col record;
    operation text;
    expected boolean;
    allowed jsonb;
    policy CONSTANT jsonb := '{
      "Products": {"SELECT":["product_id","loyverse_id","name","description","is_available","is_active","is_featured","is_new","synced_at"], "UPDATE":["description","is_available"]},
      "ProductVariants": {"SELECT":["product_variant_id","loyverse_id","name","price","is_available","is_commercially_available","synced_at","product_id"], "UPDATE":["is_available"]},
      "ProductImages": {"SELECT":["product_image_id","product_variant_id","image_url","display_order","created_at"], "INSERT":["product_variant_id","image_url","display_order"], "UPDATE":["display_order"], "DELETE":true},
      "Tags": {"SELECT":["tag_id","name","created_at"], "INSERT":["name"]},
      "ProductTags": {"SELECT":["product_variant_id","tag_id"], "INSERT":["product_variant_id","tag_id"], "DELETE":true},
      "Persons": {"SELECT":["person_id","name"]},
      "PersonalAccounts": {"SELECT":["account_id","person_id","email","password_hash","is_active","auth_version","email_verified_at","last_login_at","created_at","updated_at"], "UPDATE":["password_hash","auth_version","last_login_at","updated_at"]},
      "Employees": {"SELECT":["person_id","employee_code","hired_at","is_active","created_at"]},
      "Roles": {"SELECT":["role_id","code","name","description"]},
      "EmployeeRoles": {"SELECT":["person_id","role_id"]},
      "AccountSessions": {"SELECT":["session_id","account_id","token_hash","csrf_token_hash","auth_version","created_at","last_activity_at","expires_at","idle_expires_at","revoked_at","revocation_reason"], "INSERT":["session_id","account_id","token_hash","csrf_token_hash","auth_version","created_at","last_activity_at","expires_at","idle_expires_at","revoked_at","revocation_reason"], "UPDATE":["last_activity_at","idle_expires_at","revoked_at","revocation_reason"]},
      "AuditEvents": {"SELECT":["audit_event_id","created_at"], "INSERT":["actor_account_id","action","outcome","resource_type","resource_id","request_id","failure_code"]},
      "AuthRateLimitPolicy": {"SELECT":["policy_id","fingerprint"], "INSERT":["policy_id","fingerprint"]},
      "AuthRateLimits": {"SELECT":["key_hash","started_at","expires_at","attempts"], "INSERT":["key_hash","started_at","expires_at","attempts"], "UPDATE":["attempts"], "DELETE":true}
    }';
BEGIN
    IF current_database() <> 'mitbol_cloud_dev'
       OR current_user <> 'mitbol_admin_writer' OR session_user <> 'mitbol_admin_writer'
       OR coalesce(current_setting('mitbol.external_tls_verified', true), '') <> 'verify-full-channel-binding'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'WRITER_NEON_IDENTITY';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname = current_user;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = writer AND
       (NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = writer) THEN
        RAISE EXCEPTION 'WRITER_ATTRIBUTES_OR_MEMBERSHIP';
    END IF;
    IF NOT has_database_privilege(writer, current_database(), 'CONNECT')
       OR NOT has_schema_privilege(writer, 'public', 'USAGE')
       OR has_database_privilege(writer, current_database(), 'CREATE,TEMPORARY')
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE has_schema_privilege(writer, oid, 'CREATE')) THEN
        RAISE EXCEPTION 'WRITER_DATABASE_OR_SCHEMA';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_database WHERE datdba = writer)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = writer)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = writer)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = writer)
       OR EXISTS (SELECT 1 FROM pg_type WHERE typowner = writer)
       OR EXISTS (SELECT 1 FROM pg_extension WHERE extowner = writer)
       OR EXISTS (SELECT 1 FROM pg_largeobject_metadata WHERE lomowner = writer) THEN
        RAISE EXCEPTION 'WRITER_OWNER';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND policy ? c.relname::text) <> 14 THEN
        RAISE EXCEPTION 'WRITER_MISSING_RELATIONS';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S' AND c.relname IN
        ('Tags_tag_id_seq','ProductImages_product_image_id_seq','AuditEvents_audit_event_id_seq')) <> 3 THEN
        RAISE EXCEPTION 'WRITER_MISSING_SEQUENCES';
    END IF;
    FOR rel IN SELECT c.*, n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
    LOOP
        allowed := CASE WHEN rel.nspname = 'public' AND rel.relkind = 'r' THEN policy -> rel.relname::text ELSE NULL END;
        IF rel.relkind IN ('r','p','v','m','f') THEN
            FOR col IN SELECT attnum, attname FROM pg_attribute
                WHERE attrelid = rel.oid AND attnum > 0 AND NOT attisdropped
            LOOP
                FOREACH operation IN ARRAY ARRAY['SELECT','INSERT','UPDATE','REFERENCES'] LOOP
                    expected := coalesce((allowed -> operation) ? col.attname::text, false);
                    IF has_column_privilege(writer, rel.oid, col.attnum, operation) <> expected THEN
                        RAISE EXCEPTION 'WRITER_COLUMN: %.% %', rel.relname, col.attname, operation;
                    END IF;
                END LOOP;
            END LOOP;
            IF has_table_privilege(writer, rel.oid, 'DELETE') <> coalesce((allowed ->> 'DELETE')::boolean, false)
               OR has_table_privilege(writer, rel.oid, 'TRUNCATE,REFERENCES,TRIGGER,MAINTAIN') THEN
                RAISE EXCEPTION 'WRITER_TABLE: %', rel.relname;
            END IF;
        ELSIF rel.relkind = 'S' THEN
            expected := rel.nspname = 'public' AND rel.relname IN
                ('Tags_tag_id_seq','ProductImages_product_image_id_seq','AuditEvents_audit_event_id_seq');
            IF has_sequence_privilege(writer, rel.oid, 'USAGE') <> expected
               OR has_sequence_privilege(writer, rel.oid, 'SELECT,UPDATE') THEN
                RAISE EXCEPTION 'WRITER_SEQUENCE: %', rel.relname;
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM (
        SELECT relacl AS acl FROM pg_class UNION ALL SELECT attacl FROM pg_attribute
        UNION ALL SELECT nspacl FROM pg_namespace UNION ALL SELECT datacl FROM pg_database
        UNION ALL SELECT proacl FROM pg_proc
    ) objects CROSS JOIN LATERAL aclexplode(objects.acl) a
        WHERE a.grantee IN (writer, 0) AND a.is_grantable) THEN
        RAISE EXCEPTION 'WRITER_GRANT_OPTION';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a
        WHERE a.grantee IN (writer, 0) AND
        (d.defaclobjtype IN ('r','S','L') OR (d.defaclobjtype = 'n' AND a.privilege_type = 'CREATE') OR a.is_grantable)) THEN
        RAISE EXCEPTION 'WRITER_UNSAFE_DEFAULTS';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
          AND has_schema_privilege(writer, n.oid, 'USAGE') AND has_function_privilege(writer, p.oid, 'EXECUTE')) THEN
        RAISE EXCEPTION 'WRITER_UNREVIEWED_ROUTINES';
    END IF;
END
$policy$;
SELECT current_database() AS database, current_user AS effective_user, session_user AS login_user,
       'WRITER_POLICY_OK_REVIEWED_PROFILE' AS result;
COMMIT;

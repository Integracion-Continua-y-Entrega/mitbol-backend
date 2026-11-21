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
    queue_installed boolean := to_regclass('public."SyncRuns"') IS NOT NULL;
    permitted_sequences text[] := ARRAY['Tags_tag_id_seq','ProductImages_product_image_id_seq','AuditEvents_audit_event_id_seq'];
    revision text;
    queue_permissions jsonb := '{
      "SyncRuns": {"SELECT":["run_id","catalog_key","store_id","source","status","requested_by","created_at","started_at","finished_at","available_at","attempts","attempt_token","result","error_code"], "INSERT":["run_id","store_id","source","requested_by"]},
      "alembic_version": {"SELECT":["version_num"]},
      "ProductModifiers": {"SELECT":["synced_at"]},
      "ProductComplements": {"SELECT":["synced_at"]}
    }';
    policy jsonb := '{
      "Products": {"SELECT":["product_id","loyverse_id","name","description","is_available","is_active","is_featured","is_new","synced_at"], "UPDATE":["description","is_available","is_featured","is_new"]},
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
    IF current_database() !~ '^mitbol_test_auth_[a-f0-9]{32}$'
       OR current_user <> 'mitbol_admin_writer' OR session_user <> 'mitbol_admin_writer'
       OR coalesce(inet_server_addr() NOT IN ('127.0.0.1'::inet, '::1'::inet), true)
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'WRITER_LOCAL_IDENTITY';
    END IF;
    IF to_regclass('public."Categories"') IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012')) THEN
            RAISE EXCEPTION 'WRITER_CATEGORIES_SCHEMA';
        END IF;
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
    IF queue_installed THEN
        IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
              AND queue_permissions ? c.relname::text) <> 4
           OR NOT has_table_privilege(writer, 'public."SyncRuns"', 'SELECT')
           OR NOT has_column_privilege(writer, 'public.alembic_version', 'version_num', 'SELECT') THEN
            RAISE EXCEPTION 'WRITER_QUEUE_PERMISSIONS';
        END IF;
        IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
           (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261114_0007','20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012')) THEN
            RAISE EXCEPTION 'WRITER_QUEUE_SCHEMA';
        END IF;
        -- El puente no consulta la columna y admite el SELECT revisado del delta.
        IF EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num IN ('20261115_0008', '20261119_0009', '20261119_0010', '20261119_0011', '20261119_0012'))
           AND has_column_privilege(writer,'public."Products"','category_id','SELECT') THEN
            policy := jsonb_set(policy,'{Products,SELECT}',(policy->'Products'->'SELECT') || '["category_id"]'::jsonb);
        END IF;
    END IF;
    IF queue_installed THEN
        SELECT version_num INTO STRICT revision FROM public.alembic_version;
    END IF;
    IF revision IN ('20261119_0009','20261119_0010','20261119_0011','20261119_0012') THEN
        policy := jsonb_set(policy,'{Persons}', '{"SELECT":["person_id","name","phone","created_at","updated_at"],"INSERT":["name","phone","updated_at"],"UPDATE":["name","phone","updated_at"]}'::jsonb);
        policy := jsonb_set(policy,'{PersonalAccounts,INSERT}', '["person_id","email","password_hash","is_active","email_verified_at","last_login_at","updated_at"]'::jsonb);
        policy := policy || '{
          "RestaurantOrderSettings":{"SELECT":["settings_id","accepting_orders"],"UPDATE":["settings_id"]},
          "Orders":{"SELECT":["order_id","idempotency_key","request_hash","account_id","modality","requester_name","requester_phone","status","created_at","subtotal","discount_total","total"],"INSERT":["order_id","idempotency_key","request_hash","account_id","modality","requester_name","requester_phone","subtotal","discount_total","total"],"UPDATE":["status"]},
          "OrderDetails":{"SELECT":["order_detail_id","order_id","line_index","product_id","product_variant_id","product_name","variant_name","quantity","base_price","complements_total","unit_total","subtotal"],"INSERT":["order_id","line_index","product_id","product_variant_id","product_name","variant_name","quantity","base_price","complements_total","unit_total","subtotal"]},
          "OrderDetailComplements":{"SELECT":["order_detail_complement_id","order_detail_id","product_complement_id","product_modifier_id","position","name_snapshot","unit_price"],"INSERT":["order_detail_id","product_complement_id","product_modifier_id","position","name_snapshot","unit_price"]},
          "ProductModifierAssignments":{"SELECT":["product_variant_id","product_modifier_id"]}
        }'::jsonb;
        queue_permissions := queue_permissions || '{
          "ProductModifiers":{"SELECT":["product_modifier_id","loyverse_id","name","display_order","synced_at"]},
          "ProductComplements":{"SELECT":["product_complement_id","product_modifier_id","loyverse_id","name","price","is_active","synced_at","display_order"]}
        }'::jsonb;
        permitted_sequences := permitted_sequences || ARRAY['Persons_person_id_seq','PersonalAccounts_account_id_seq',
            'OrderDetails_order_detail_id_seq','OrderDetailComplements_order_detail_complement_id_seq'];
    END IF;
    IF revision IN ('20261119_0010','20261119_0011','20261119_0012') THEN
        FOREACH operation IN ARRAY ARRAY['SELECT','INSERT','UPDATE'] LOOP
            policy := jsonb_set(policy,ARRAY['Orders',operation],(policy->'Orders'->operation) || '["guest_tracking_token"]'::jsonb);
        END LOOP;
    END IF;
    IF revision IN ('20261119_0011','20261119_0012') THEN
        policy := policy || '{"PasswordResetTokens":{"SELECT":["account_id","token_hash","auth_version","created_at","expires_at"],"INSERT":["account_id","token_hash","auth_version","created_at","expires_at"],"UPDATE":["token_hash","auth_version","created_at","expires_at"],"DELETE":true}}'::jsonb;
    END IF;
    IF revision = '20261119_0012' THEN
        policy := policy || '{"CustomerCoupons":{"SELECT":["customer_coupon_id","account_id","name","description","discount_id","starts_at","expires_at","status","conditions","created_at"]}}'::jsonb;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(policy) k(name)
               WHERE to_regclass(format('public.%I',k.name)) IS NULL) THEN
        RAISE EXCEPTION 'WRITER_MISSING_RELATIONS';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S' AND c.relname IN
        (SELECT unnest(permitted_sequences))) <> cardinality(permitted_sequences) THEN
        RAISE EXCEPTION 'WRITER_MISSING_SEQUENCES';
    END IF;
    FOR rel IN SELECT c.*, n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
    LOOP
        allowed := CASE WHEN rel.nspname = 'public' AND rel.relkind = 'r' THEN policy -> rel.relname::text ELSE NULL END;
        IF queue_installed AND rel.nspname = 'public' AND rel.relkind = 'r'
           AND queue_permissions ? rel.relname::text THEN
            allowed := queue_permissions -> rel.relname::text;
        END IF;
        IF rel.nspname = 'public' AND rel.relkind = 'r' AND rel.relname = 'Discounts' THEN
            allowed := '{"SELECT":["discount_id","loyverse_id","name","discount_type","value","scope","is_active","synced_at","created_at"]}'::jsonb;
        END IF;
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
            expected := rel.nspname = 'public' AND rel.relname = ANY(permitted_sequences);
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
       'WRITER_POLICY_OK_LOCAL_ONLY' AS result;
COMMIT;

-- PROPUESTA NO EJECUTADA: delta aditivo para el escritor EXISTENTE.
-- Sin roles nuevos, datos comerciales, credenciales ni cambios de configuración.
-- Requiere revisión explícita y transporte/destino contrastados por el operador.
BEGIN;
SET LOCAL search_path = pg_catalog, public;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '5s';
DO $guard$
DECLARE
    writer oid;
BEGIN
    IF coalesce(current_setting('mitbol.approved_admin_write', true), '') <> 'db-compat-0012-reviewed'
       OR current_setting('server_version_num')::int / 10000 <> 18 THEN
        RAISE EXCEPTION 'RECENT_NOT_APPROVED';
    END IF;
    IF NOT coalesce(
        (current_database() ~ '^mitbol_test_auth_[a-f0-9]{32}$'
         AND current_user = 'mitbol_ci' AND session_user = 'mitbol_ci'
         AND inet_server_addr() IN ('127.0.0.1'::inet, '::1'::inet))
        OR
        (current_database() = 'mitbol_cloud_dev'
         AND current_user = 'neondb_owner' AND session_user = 'neondb_owner'
         AND current_setting('mitbol.external_tls_verified', true) = 'verify-full-channel-binding')
    , false) THEN
        RAISE EXCEPTION 'RECENT_WRONG_TARGET_OR_OPERATOR';
    END IF;
    IF (SELECT count(*) FROM public.alembic_version) <> 1 OR NOT EXISTS
       (SELECT 1 FROM public.alembic_version WHERE version_num IN
        ('20261119_0009','20261119_0010','20261119_0011','20261119_0012','20261120_0013')) THEN
        RAISE EXCEPTION 'RECENT_WRONG_SCHEMA';
    END IF;
    SELECT oid INTO writer FROM pg_roles WHERE rolname='mitbol_admin_writer'
        AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit;
    IF writer IS NULL OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=writer) THEN
        RAISE EXCEPTION 'RECENT_UNSAFE_WRITER';
    END IF;
END
$guard$;
DO $recent$
DECLARE
    revision text := (SELECT version_num FROM public.alembic_version);
BEGIN
    IF revision IN ('20261119_0009','20261119_0010','20261119_0011','20261119_0012','20261120_0013') THEN
        GRANT SELECT (phone,created_at,updated_at), INSERT (name,phone,updated_at), UPDATE (name,phone,updated_at)
            ON public."Persons" TO mitbol_admin_writer;
        GRANT INSERT (person_id,email,password_hash,is_active,email_verified_at,last_login_at,updated_at)
            ON public."PersonalAccounts" TO mitbol_admin_writer;
        GRANT USAGE ON SEQUENCE public."Persons_person_id_seq", public."PersonalAccounts_account_id_seq",
            public."OrderDetails_order_detail_id_seq", public."OrderDetailComplements_order_detail_complement_id_seq"
            TO mitbol_admin_writer;
        GRANT SELECT (product_modifier_id,loyverse_id,name,display_order,synced_at)
            ON public."ProductModifiers" TO mitbol_admin_writer;
        GRANT SELECT (product_complement_id,product_modifier_id,loyverse_id,name,price,is_active,synced_at,display_order)
            ON public."ProductComplements" TO mitbol_admin_writer;
        GRANT SELECT (product_variant_id,product_modifier_id)
            ON public."ProductModifierAssignments" TO mitbol_admin_writer;
        GRANT SELECT (settings_id,accepting_orders), UPDATE (settings_id)
            ON public."RestaurantOrderSettings" TO mitbol_admin_writer;
        -- FOR SHARE requiere UPDATE en alguna columna. La PK singleton no permite
        -- cambiar el identificador; no se autoriza UPDATE de accepting_orders.
        GRANT SELECT (order_id,idempotency_key,request_hash,account_id,modality,requester_name,requester_phone,
                      status,created_at,subtotal,discount_total,total),
              INSERT (order_id,idempotency_key,request_hash,account_id,modality,requester_name,requester_phone,
                      subtotal,discount_total,total), UPDATE (status)
            ON public."Orders" TO mitbol_admin_writer;
        GRANT SELECT (order_detail_id,order_id,line_index,product_id,product_variant_id,product_name,variant_name,
                      quantity,base_price,complements_total,unit_total,subtotal),
              INSERT (order_id,line_index,product_id,product_variant_id,product_name,variant_name,
                      quantity,base_price,complements_total,unit_total,subtotal)
            ON public."OrderDetails" TO mitbol_admin_writer;
        GRANT SELECT (order_detail_complement_id,order_detail_id,product_complement_id,product_modifier_id,
                      position,name_snapshot,unit_price),
              INSERT (order_detail_id,product_complement_id,product_modifier_id,position,name_snapshot,unit_price)
            ON public."OrderDetailComplements" TO mitbol_admin_writer;
    END IF;
    IF revision IN ('20261119_0010','20261119_0011','20261119_0012','20261120_0013') THEN
        GRANT SELECT (guest_tracking_token), INSERT (guest_tracking_token), UPDATE (guest_tracking_token)
            ON public."Orders" TO mitbol_admin_writer;
    END IF;
    IF revision IN ('20261119_0011','20261119_0012','20261120_0013') THEN
        GRANT SELECT (account_id,token_hash,auth_version,created_at,expires_at),
              INSERT (account_id,token_hash,auth_version,created_at,expires_at),
              UPDATE (token_hash,auth_version,created_at,expires_at), DELETE
            ON public."PasswordResetTokens" TO mitbol_admin_writer;
    END IF;
    IF revision IN ('20261119_0012','20261120_0013') THEN
        GRANT SELECT (customer_coupon_id,account_id,name,description,discount_id,starts_at,expires_at,status,conditions,created_at)
            ON public."CustomerCoupons" TO mitbol_admin_writer;
    END IF;
    IF revision = '20261120_0013' THEN
        GRANT SELECT (discount_id,product_id,conditions)
            ON public."CommercialDiscountAssignments" TO mitbol_admin_writer;
        GRANT SELECT (order_id,calculation), INSERT (order_id,calculation)
            ON public."OrderDiscountSnapshots" TO mitbol_admin_writer;
    END IF;
END
$recent$;
COMMIT;

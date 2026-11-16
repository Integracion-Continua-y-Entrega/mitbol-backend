-- Contrato de prueba equivalente al upgrade revisado del PR #15.
-- No es una migración ni se utiliza desde la aplicación o los comandos operativos.
DO $guard$
BEGIN
 IF current_database() !~ '^mitbol_test_auth_[a-f0-9]{32}$' OR current_user<>'mitbol_ci'
    OR inet_server_addr() NOT IN ('127.0.0.1'::inet,'::1'::inet)
    OR (SELECT count(*) FROM public.alembic_version)<>1
    OR NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='20261114_0007') THEN
  RAISE EXCEPTION 'BRIDGE_FIXTURE_REQUIRES_ISOLATED_LOCAL_0007';
 END IF;
END $guard$;
CREATE TABLE public."Categories" (
 category_id SERIAL NOT NULL,
 loyverse_id VARCHAR(100) NOT NULL,
 name VARCHAR(64) NOT NULL,
 color VARCHAR(20),
 is_active BOOLEAN DEFAULT true NOT NULL,
 synced_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT "pk_Categories" PRIMARY KEY(category_id),
 CONSTRAINT "uq_Categories_loyverse_id" UNIQUE(loyverse_id)
);
ALTER TABLE public."Products" ADD COLUMN category_id INTEGER;
ALTER TABLE public."Products" ADD CONSTRAINT "fk_Products_category_id"
 FOREIGN KEY(category_id) REFERENCES public."Categories"(category_id) ON DELETE RESTRICT;
CREATE INDEX "ix_Products_category_id" ON public."Products"(category_id);
UPDATE public.alembic_version SET version_num='20261115_0008';

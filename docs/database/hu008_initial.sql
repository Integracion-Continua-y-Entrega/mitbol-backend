-- SQL de referencia: revisión aplicada el 2026-10-29 mediante Alembic.
-- Este archivo no se ejecutó directamente; conservar para consultar el DDL.
BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20261029_0001

CREATE TABLE "Products" (
    product_id SERIAL NOT NULL,
    loyverse_id VARCHAR(100) NOT NULL,
    name VARCHAR(150) NOT NULL,
    description TEXT,
    is_available BOOLEAN DEFAULT true NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    is_featured BOOLEAN DEFAULT false NOT NULL,
    is_new BOOLEAN DEFAULT true NOT NULL,
    synced_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT "pk_Products" PRIMARY KEY (product_id),
    CONSTRAINT "uq_Products_loyverse_id" UNIQUE (loyverse_id)
);

CREATE TABLE "ProductModifiers" (
    product_modifier_id SERIAL NOT NULL,
    loyverse_id VARCHAR(100) NOT NULL,
    name VARCHAR(150) NOT NULL,
    display_order INTEGER NOT NULL,
    synced_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT "pk_ProductModifiers" PRIMARY KEY (product_modifier_id),
    CONSTRAINT "uq_ProductModifiers_loyverse_id" UNIQUE (loyverse_id)
);

CREATE TABLE "ProductVariants" (
    product_variant_id SERIAL NOT NULL,
    loyverse_id VARCHAR(100) NOT NULL,
    name VARCHAR(150) NOT NULL,
    price NUMERIC NOT NULL,
    is_available BOOLEAN DEFAULT true NOT NULL,
    synced_at TIMESTAMP WITH TIME ZONE NOT NULL,
    product_id INTEGER NOT NULL,
    CONSTRAINT "pk_ProductVariants" PRIMARY KEY (product_variant_id),
    CONSTRAINT "uq_ProductVariants_loyverse_id" UNIQUE (loyverse_id),
    CONSTRAINT "fk_ProductVariants_product_id" FOREIGN KEY(product_id) REFERENCES "Products" (product_id)
);

CREATE TABLE "ProductComplements" (
    product_complement_id SERIAL NOT NULL,
    product_modifier_id INTEGER NOT NULL,
    loyverse_id VARCHAR(100) NOT NULL,
    name VARCHAR(150) NOT NULL,
    price NUMERIC NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    synced_at TIMESTAMP WITH TIME ZONE NOT NULL,
    display_order INTEGER NOT NULL,
    CONSTRAINT "pk_ProductComplements" PRIMARY KEY (product_complement_id),
    CONSTRAINT "uq_ProductComplements_loyverse_id" UNIQUE (loyverse_id),
    CONSTRAINT "fk_ProductComplements_product_modifier_id" FOREIGN KEY(product_modifier_id) REFERENCES "ProductModifiers" (product_modifier_id)
);

CREATE TABLE "ProductModifierAssignments" (
    product_variant_id INTEGER NOT NULL,
    product_modifier_id INTEGER NOT NULL,
    CONSTRAINT "pk_ProductModifierAssignments" PRIMARY KEY (product_variant_id, product_modifier_id),
    CONSTRAINT "fk_ProductModifierAssignments_product_variant_id" FOREIGN KEY(product_variant_id) REFERENCES "ProductVariants" (product_variant_id),
    CONSTRAINT "fk_ProductModifierAssignments_product_modifier_id" FOREIGN KEY(product_modifier_id) REFERENCES "ProductModifiers" (product_modifier_id)
);

INSERT INTO alembic_version (version_num) VALUES ('20261029_0001') RETURNING alembic_version.version_num;

COMMIT;


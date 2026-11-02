-- HU-005: SQL revisado y autorizado; revision aplicada en mitbol_dev el 2026-11-02.
-- Generado offline y contrastado con Alembic antes de aplicar 20261102_0002.

BEGIN;

-- Running upgrade 20261029_0001 -> 20261102_0002

CREATE TABLE "ProductImages" (
    product_image_id SERIAL NOT NULL,
    product_variant_id INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT "pk_ProductImages" PRIMARY KEY (product_image_id),
    CONSTRAINT "fk_ProductImages_product_variant_id" FOREIGN KEY(product_variant_id) REFERENCES "ProductVariants" (product_variant_id)
);

CREATE TABLE "Tags" (
    tag_id SERIAL NOT NULL,
    name VARCHAR(150) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT "pk_Tags" PRIMARY KEY (tag_id),
    CONSTRAINT "uq_Tags_name" UNIQUE (name)
);

CREATE TABLE "ProductTags" (
    product_variant_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    CONSTRAINT "pk_ProductTags" PRIMARY KEY (product_variant_id, tag_id),
    CONSTRAINT "fk_ProductTags_product_variant_id" FOREIGN KEY(product_variant_id) REFERENCES "ProductVariants" (product_variant_id),
    CONSTRAINT "fk_ProductTags_tag_id" FOREIGN KEY(tag_id) REFERENCES "Tags" (tag_id)
);

UPDATE alembic_version SET version_num='20261102_0002' WHERE alembic_version.version_num = '20261029_0001';

COMMIT;


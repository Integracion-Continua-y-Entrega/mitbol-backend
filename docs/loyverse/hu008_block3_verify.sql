-- Ejecutar conectado a mitbol_dev. Todo el archivo es de sólo lectura.
BEGIN TRANSACTION READ ONLY;

SELECT current_database() AS database_name,
       current_setting('transaction_read_only') AS read_only;
SELECT version_num FROM public.alembic_version;

-- Conteos confirmados: 3, 5, 2, 5 y 4 respectivamente.
SELECT 'Products' AS tabla, count(*) AS filas FROM public."Products"
UNION ALL SELECT 'ProductVariants', count(*) FROM public."ProductVariants"
UNION ALL SELECT 'ProductModifiers', count(*) FROM public."ProductModifiers"
UNION ALL SELECT 'ProductComplements', count(*) FROM public."ProductComplements"
UNION ALL SELECT 'ProductModifierAssignments', count(*) FROM public."ProductModifierAssignments";

-- Identidades externas e internas, nombres y configuración local inicial.
SELECT product_id, loyverse_id, name, description, is_available,
       is_active, is_featured, is_new, synced_at
FROM public."Products" ORDER BY product_id;

-- Cada variante, su producto correcto y su precio comercial.
SELECT p.product_id, p.loyverse_id AS product_loyverse_id, p.name AS producto,
       v.product_variant_id, v.loyverse_id AS variant_loyverse_id,
       v.name AS variante, v.price, v.is_available, v.synced_at
FROM public."ProductVariants" v
JOIN public."Products" p ON p.product_id = v.product_id
ORDER BY p.product_id, v.product_variant_id;

-- Grupos y complementos: precios, orden e identidades.
SELECT m.product_modifier_id, m.loyverse_id AS modifier_loyverse_id,
       m.name AS grupo, m.display_order AS group_order,
       c.product_complement_id, c.loyverse_id AS complement_loyverse_id,
       c.name AS complemento, c.price, c.display_order, c.is_active
FROM public."ProductModifiers" m
LEFT JOIN public."ProductComplements" c ON c.product_modifier_id = m.product_modifier_id
ORDER BY m.display_order, c.display_order;

-- Deben aparecer cuatro parejas: dos tamaños de limonada y dos hamburguesas.
SELECT p.name AS producto, v.name AS variante, m.name AS grupo,
       a.product_variant_id, a.product_modifier_id
FROM public."ProductModifierAssignments" a
JOIN public."ProductVariants" v ON v.product_variant_id = a.product_variant_id
JOIN public."Products" p ON p.product_id = v.product_id
JOIN public."ProductModifiers" m ON m.product_modifier_id = a.product_modifier_id
ORDER BY a.product_variant_id, a.product_modifier_id;

-- Todas las comprobaciones siguientes deben devolver cero.
SELECT 'Products' AS tabla, count(*) AS grupos_duplicados FROM (
    SELECT loyverse_id FROM public."Products" GROUP BY loyverse_id HAVING count(*) > 1
) d
UNION ALL SELECT 'ProductVariants', count(*) FROM (
    SELECT loyverse_id FROM public."ProductVariants" GROUP BY loyverse_id HAVING count(*) > 1
) d
UNION ALL SELECT 'ProductModifiers', count(*) FROM (
    SELECT loyverse_id FROM public."ProductModifiers" GROUP BY loyverse_id HAVING count(*) > 1
) d
UNION ALL SELECT 'ProductComplements', count(*) FROM (
    SELECT loyverse_id FROM public."ProductComplements" GROUP BY loyverse_id HAVING count(*) > 1
) d
UNION ALL SELECT 'ProductModifierAssignments', count(*) FROM (
    SELECT product_variant_id, product_modifier_id FROM public."ProductModifierAssignments"
    GROUP BY product_variant_id, product_modifier_id HAVING count(*) > 1
) d;

SELECT 'Variantes sin producto' AS comprobacion, count(*) AS referencias_invalidas
FROM public."ProductVariants" v
LEFT JOIN public."Products" p ON p.product_id = v.product_id WHERE p.product_id IS NULL
UNION ALL
SELECT 'Complementos sin grupo', count(*) FROM public."ProductComplements" c
LEFT JOIN public."ProductModifiers" m ON m.product_modifier_id = c.product_modifier_id
WHERE m.product_modifier_id IS NULL
UNION ALL
SELECT 'Asignaciones sin variante', count(*) FROM public."ProductModifierAssignments" a
LEFT JOIN public."ProductVariants" v ON v.product_variant_id = a.product_variant_id
WHERE v.product_variant_id IS NULL
UNION ALL
SELECT 'Asignaciones sin grupo', count(*) FROM public."ProductModifierAssignments" a
LEFT JOIN public."ProductModifiers" m ON m.product_modifier_id = a.product_modifier_id
WHERE m.product_modifier_id IS NULL;

SELECT 'ProductVariants' AS tabla, count(*) AS precios_invalidos
FROM public."ProductVariants"
WHERE price IS NULL OR price < 0 OR price::text IN ('NaN', 'Infinity', '-Infinity')
UNION ALL
SELECT 'ProductComplements', count(*) FROM public."ProductComplements"
WHERE price IS NULL OR price < 0 OR price::text IN ('NaN', 'Infinity', '-Infinity');

ROLLBACK;

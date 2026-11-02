-- Ejecutar en pgAdmin conectado a mitbol_dev. Todo es de sólo lectura.
BEGIN TRANSACTION READ ONLY;

SELECT current_database() AS database_name,
       current_setting('transaction_read_only') AS read_only;

-- Debe devolver exactamente la variante 5 del producto 3 con precio 129.00.
SELECT p.product_id, p.loyverse_id AS product_loyverse_id, p.name AS producto,
       v.product_variant_id, v.loyverse_id AS variant_loyverse_id,
       v.name AS variante, v.price, v.is_available, v.synced_at
FROM public."ProductVariants" v
JOIN public."Products" p ON p.product_id = v.product_id
WHERE v.loyverse_id = '246aad80-4540-4c1a-8877-2e31bc03d668';

-- Comparación de las cinco variantes con el estado autorizado.
-- Debe devolver cinco filas y correcto=true en todas.
WITH expected (id, product_id, loyverse_id, price) AS (VALUES
    (1, 1, 'e18a3b39-eda7-4852-9ece-6f02a6f5cef5', 45.00),
    (2, 2, '128b68f8-4044-482f-b095-a6023e33f24f', 35.00),
    (3, 2, 'ea50f13f-9246-4892-8c29-b9da20e741a1', 49.00),
    (4, 3, 'f3d99230-acc8-4d58-b83f-08abb478b046', 89.00),
    (5, 3, '246aad80-4540-4c1a-8877-2e31bc03d668', 129.00)
)
SELECT e.id AS expected_id, v.product_variant_id, v.name,
       e.price AS expected_price, v.price AS stored_price,
       (v.product_variant_id IS NOT DISTINCT FROM e.id
        AND v.product_id IS NOT DISTINCT FROM e.product_id
        AND v.loyverse_id IS NOT DISTINCT FROM e.loyverse_id
        AND v.price IS NOT DISTINCT FROM e.price) AS correcto
FROM expected e
FULL JOIN public."ProductVariants" v ON v.product_variant_id = e.id
ORDER BY coalesce(e.id, v.product_variant_id);

-- Los cinco complementos deben conservar identidad, grupo y precio.
WITH expected (id, modifier_id, loyverse_id, price) AS (VALUES
    (1, 1, '44f93f48-d3c9-425d-8aa4-381f40ba12ec', 5.00),
    (2, 1, 'ea950531-f90f-4d27-83db-31c697153a43', 5.00),
    (3, 2, '299df615-6db6-489d-a526-4e6d8bf667a3', 15.00),
    (4, 2, '254a728c-07f4-4498-b976-1d18d9e9fe16', 20.00),
    (5, 2, '5c8553d8-1fec-44b2-bfdf-64257d3bf38c', 0.00)
)
SELECT e.id AS expected_id, c.product_complement_id, c.name,
       e.price AS expected_price, c.price AS stored_price,
       (c.product_complement_id IS NOT DISTINCT FROM e.id
        AND c.product_modifier_id IS NOT DISTINCT FROM e.modifier_id
        AND c.loyverse_id IS NOT DISTINCT FROM e.loyverse_id
        AND c.price IS NOT DISTINCT FROM e.price) AS correcto
FROM expected e
FULL JOIN public."ProductComplements" c ON c.product_complement_id = e.id
ORDER BY coalesce(e.id, c.product_complement_id);

-- Duplicados de variantes: debe devolver cero filas.
SELECT loyverse_id, count(*) AS repetitions
FROM public."ProductVariants"
GROUP BY loyverse_id HAVING count(*) > 1;

ROLLBACK;

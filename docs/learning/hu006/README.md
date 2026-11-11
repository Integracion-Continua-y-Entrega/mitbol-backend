# HU-006 — Administrar destacados y novedades

## Estado y alcance

Backend implementado y verificado: 1,478 pruebas aprobadas el 11 de noviembre
de 2026. Publicación en Render/Neon y aceptación del frontend pendientes. No se ha modificado el
entorno remoto durante esta implementación.

Fuente de requisitos: `Por hacer .pdf`, páginas 24–25. La historia permite
consultar, marcar y desmarcar productos destacados y novedades, publicar sus
indicadores y conservarlos durante sincronizaciones comerciales.

Los campos `Products.is_featured` e `Products.is_new` ya existían: no se crean
tablas, columnas o cuentas, ni se migran datos del catálogo.

## Contrato administrativo

| Método y ruta | Acción | Permiso de aplicación |
|---|---|---|
| `GET /api/admin/products/{product_id}/highlights` | Consulta los indicadores almacenados. | `catalog:read`: Admin y Staff. |
| `PUT /api/admin/products/{product_id}/highlights` | Reemplaza ambos indicadores. | `settings:write`: Admin. |

El modo individual reutiliza sesión, Origin, CSRF y auditoría existentes.
El modo legado conserva su restricción de administración local. No se habilitan
flags de autenticación o administración automáticamente.

PUT recibe ambos campos obligatorios:

```json
{"is_featured": true, "is_new": false}
```

GET y PUT devuelven:

```json
{"product_id": 3, "is_featured": true, "is_new": false}
```

Los valores deben ser booleanos JSON. Se rechazan null, strings, números,
campos omitidos y campos adicionales. Los IDs deben ser enteros positivos
dentro del rango admitido para el catálogo.

| HTTP | Resultado |
|---|---|
| `200` | Consulta o modificación exitosa. |
| `401` | Falta sesión válida. |
| `403` | Permiso, CSRF u origen no autorizado. |
| `404` | Producto inexistente o ruta deshabilitada por configuración. |
| `409` | Otra transacción conserva el bloqueo de catálogo. |
| `422` | Identificador o cuerpo inválido. |
| `500` / `503` | Error interno o recurso temporalmente no disponible, sin secretos. |

La consulta administrativa permite inspeccionar productos comercialmente
inactivos; eso no los publica. Una operación rechazada no confirma cambios ni
registra una auditoría de éxito de catálogo.

## Contrato público y reglas

`GET /api/menu` y `GET /api/menu/{product_id}` añaden `is_featured` e `is_new`
al producto. Son campos obligatorios de respuesta, no de variante. El detalle
hereda el contrato y reutiliza la construcción del listado.

- Las marcas son independientes y manuales; las cuatro combinaciones son válidas.
- False retira una marca. No se calcula novedad a partir de fechas.
- No cambia activación comercial, disponibilidad, precios, variantes o etiquetas.
- Un agotado conserva sus marcas y continúa sin poder pedirse cuando corresponda.
- Un producto comercialmente inactivo no se muestra por tener marcas.
- Se conserva el orden por ID y los filtros de HU-002; no se añade ordenamiento.
- Loyverse no sobrescribe estos valores locales durante sincronización.
- Se conservan valores existentes y el default actual `is_new=true` de la base;
  no se reinicializan productos como parte de esta entrega.

## Implementación y seguridad

| Archivo | Responsabilidad |
|---|---|
| [highlights.py](../../../app/schemas/highlights.py) | Entrada estricta y respuesta administrativa. |
| [product_highlights_service.py](../../../app/services/product_highlights_service.py) | Consulta, bloqueo y edición de los indicadores. |
| [admin_catalog.py](../../../app/api/admin_catalog.py) | Rutas, permisos de aplicación y auditoría. |
| [menu.py](../../../app/schemas/menu.py) | Campos públicos y ejemplos OpenAPI. |
| [menu_service.py](../../../app/services/menu_service.py) | Lectura de los valores almacenados para listado y detalle. |

La edición adquiere el bloqueo transaccional de catálogo utilizado por
sincronización. Si está ocupado devuelve 409; al liberarlo se puede reintentar.
`commit_changes` deja la confirmación a la dependencia autenticada, para que
modificación y auditoría compartan transacción. Si la auditoría falla, se revierte
la modificación.

`mitbol_admin_writer` obtiene UPDATE por columna de los dos indicadores,
además de descripción y disponibilidad local. No recibe UPDATE sobre la tabla
completa ni permisos para modificar nombre, activación comercial, precios,
identificadores o registros comerciales. El lector conserva solo lectura.

El rol SQL es compartido por operaciones administrativas; la aplicación aplica
la diferencia Admin/Staff. Se verifican ambas capas, sin confundir roles de
aplicación con identidades PostgreSQL.

## Pruebas y reproducción

La cobertura incluye:

- Cuatro combinaciones de marcas, listado y detalle, disponibles y agotados.
- Productos inactivos excluidos y conservación de variantes/precios.
- Login, CSRF, lectura Staff, edición Admin y rechazo de edición Staff.
- Entradas inválidas y producto inexistente sin modificar catálogo.
- Persistencia con LOGIN SQL limitado y auditoría en la misma transacción.
- Rollback al fallar auditoría y conflicto/reintento tras bloqueo de catálogo.
- Conservación de indicadores durante sincronización.
- Denegación de escrituras comerciales y matrices local/remota coherentes.
- Incremento/reversión de grants, idempotencia y rechazo de destino u operador erróneo.

Archivos principales: [test_highlights_http.py](../../../tests/test_highlights_http.py),
[test_auth_admin_api.py](../../../tests/test_auth_admin_api.py),
[test_admin_writer.py](../../../tests/test_admin_writer.py),
[test_menu.py](../../../tests/test_menu.py).

El laboratorio usa PostgreSQL 18 temporal con puertos aleatorios en loopback,
sin `.env`, `mitbol_dev`, Neon o Loyverse real. Para evitar ocupar el disco del
sistema puede usarse una carpeta temporal en `storage`, ignorada por Git:

```powershell
New-Item -ItemType Directory -Path '.\storage\test-lab' -Force | Out-Null
$env:TEMP = (Resolve-Path '.\storage\test-lab').Path
$env:TMP = $env:TEMP
$env:MITBOL_TEST_PG_BIN = 'D:\PostgreSQL\18\bin'
.\venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader tests -x -q
.\venv\Scripts\python.exe scripts/render_admin_deployment_sql.py --check
```

Los cambios a TEMP/TMP son solo para esa terminal. Los clústeres se detienen al
finalizar; los archivos se conservan para diagnóstico.

Validación final del 11 de noviembre de 2026: **1,478 pruebas aprobadas**, sin
fallos ni omisiones, con PostgreSQL 18.6 en clústeres temporales. Duración:
846.92 segundos (14 min 06 s). Dos avisos de deprecación de dependencias
existentes. También pasaron `pip check`, la regeneración/verificación de perfiles
SQL y las comprobaciones de enlaces y whitespace.

La corrida final usa temporales en D: porque una corrida anterior no pudo
completarse por falta de espacio en C:. La evidencia y los datos temporales
quedaron en `storage/test-lab`, fuera del control de versiones.

## Despliegue y aceptación pendientes

El verificador cloud exige privilegios exactos. Publicar código nuevo sobre un
escritor con el perfil anterior puede impedir conexiones administrativas.
Antes de fusionar en una rama que despliegue automáticamente, seguir el
[procedimiento de publicación](../../deployment/hu006_rollout.md).

El incremento para un rol existente está en
[update_admin_writer_highlights.sql](../../database/update_admin_writer_highlights.sql).
Los scripts `prepare_admin_writer_*` crean roles nuevos y no sustituyen ese
incremento. Los scripts remotos preparados no se ejecutaron durante las pruebas.

La historia compartida requiere comprobar desde frontend controles Admin,
indicadores visuales, retirada de marcas y disponibilidad. El backend local
verificado no acredita que la versión esté publicada ni aceptada en el entorno
compartido. No cerrar la historia de extremo a extremo sin esa evidencia.

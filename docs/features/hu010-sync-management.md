# HU-010 — Administración de sincronizaciones

Estado: consulta administrativa de fechas integrada; ejecución y actividad pendientes. Rama
`feature/hu-010-sync-management`, creada desde develop `7d9b1c6`, que integra HU-009.
Se añadió el GET protegido y se prepararon grants mínimos. No hay nuevos
procesos de sincronización o despliegues activados.

## Alcance y límite de aceptación

Fuente: `Por hacer .pdf`, páginas 28–30. Consultar información básica de
sincronización, solicitar actualizaciones administrativas, evitar concurrencia
y conservar datos válidos ante fallos. Reutilizar los procesos comerciales de
HU-008 y HU-009 y coordinar ejecuciones manuales y periódicas.

El documento excluye nuevas tablas en esta etapa y deja pendiente un mecanismo
persistente para el historial completo. También establece que HU-010 no puede
marcarse completamente terminada mientras ese criterio no se cumpla. Este
desarrollo implementará el alcance disponible sin presentar `synced_at` como
un historial de ejecuciones ni añadir tablas por iniciativa propia.

## Piezas disponibles

- Servicios transaccionales de catálogo y descuentos, con bloqueo comercial
  compartido y validación de destino.
- Comandos manuales separados del pool web, con preflight y ejecución explícita.
- Fechas `synced_at` en Products, ProductVariants, ProductModifiers,
  ProductComplements y Discounts.
- Autenticación, protección CSRF/Origin y permisos administrativos existentes.

No se identificó un planificador periódico activo en el código revisado. Se
deberá preparar una entrada reutilizable con el mismo bloqueo; la programación
en infraestructura y el despliegue se gestionarán por separado.

## Bloques de implementación

| Bloque | Resultado verificable |
|---|---|
| 1 | Consulta agregada por entidad: total de filas, filas con fecha y última fecha observada. |
| 2 | Esquemas, servicio y GET protegido; diferencias entre tabla vacía, fechas ausentes y datos conocidos. |
| 3 | Coordinación de ejecución, observación de actividad y rechazo de simultaneidad, sin inventar historial. |
| 4 | Operación manual autorizada que reutilice los servicios y conserve la separación entre rol web y operador. |
| 5 | Entrada periódica reutilizable, errores seguros y pruebas de convivencia con solicitudes manuales. |
| 6 | Regresiones, contrato para frontend, documentación y commits por bloque funcional. |

Antes del bloque 4 se definirá el canal entre la solicitud web y el ejecutor:
no se concederá DML comercial al rol web ni se expondrán credenciales de Loyverse
a React. La ejecución real permanecerá deshabilitada hasta disponer de una
configuración operativa explícita. No se reactivará el router legado de sincronización.

Propuesta inicial de permisos: `catalog:read` para consultar estado y
`settings:write` para solicitar ejecuciones (Admin, no Staff). Reutiliza los
códigos existentes; no requiere nuevas tablas de permisos.

## Semántica del estado

`max(synced_at)` indica la fecha más reciente entre las filas actuales de una
entidad. No prueba que todo el catálogo se sincronizara, que la última ejecución
terminara bien o que no haya una ejecución en curso. Un lote vacío tampoco deja
evidencia de ejecución en esas filas. El estado de actividad requerirá un
mecanismo independiente basado en la coordinación real, no en esas fechas.

El primer ejercicio en `sync_status_queries.py` solo construye SQL. No cambia
la revisión Alembic, no ejecuta consultas y no contacta al proveedor.

## Verificación del primer bloque

Se aprobaron **11 pruebas en PostgreSQL 18 temporal**, en 3.07 segundos, el
12 de noviembre de 2026. Se comprobaron las cinco entidades vacías, mezcla de
fechas y nulos, registros inactivos, fechas equivalentes en distintas zonas y
rechazo de tablas no admitidas. El esquema del laboratorio se creó desde los
modelos existentes y se revirtió al terminar; no se agregaron migraciones.
No se consultaron bases de desarrollo o cloud ni Loyverse real.

`EntitySyncSummary` en `app/schemas/sync_status.py` valida conteos enteros no
negativos, campos obligatorios, conteo con fecha no superior al total y
consistencia entre fecha y registros fechados. Exige zona horaria y normaliza
la fecha a UTC. Se aprobaron **19 pruebas del esquema** en 0.16 segundos,
sin abrir PostgreSQL. El esquema todavía no forma parte de un contrato HTTP.

`sync_status_service.py` prepara una sentencia UNION ALL de las cinco entidades
para evitar mezclar snapshots entre consultas separadas. Ejecuta la consulta y
devuelve cinco esquemas, conservando la transacción del llamador. Una tabla
faltante produce un error y no se presenta un estado incompleto.

Verificación conjunta: **33 pruebas aprobadas** en 3.70 segundos sobre
PostgreSQL 18 temporal. Además de agregaciones y esquema, se comprobaron las
cinco entidades vacías, lectura en una sola sentencia, conservación de datos
y sesión y fallo ante una entidad ausente. En ese primer corte aún no se
modificaban ACL o rutas.

## Contrato de consulta

`GET /api/admin/sync/status` se monta con el catálogo administrativo existente:
sesión con `catalog:read` (Admin/Staff) o guard local legado. No está disponible
en la API pública con la administración deshabilitada.

La respuesta contiene `entities`, con cinco resúmenes, y
`history_available=false`. Cada entidad incluye nombre, total_count,
synced_count y last_synced_at (UTC o null). Incluso una base vacía devuelve
cinco resúmenes. No expone is_running, éxito, última ejecución o historial.
No contacta a Loyverse. Errores: 401/403 por acceso, 503 si faltan tablas o
permisos requeridos, y errores internos sin detalles de infraestructura.

## Perfil SQL y actualización operativa

El rol web añade únicamente SELECT(synced_at) de ProductModifiers y
ProductComplements; los demás datos del resumen ya eran legibles. No se concede
SELECT de nombres/precios de esas dos tablas, DML comercial o nuevas secuencias.
El lector público conserva su perfil. No se agrega ninguna migración o tabla.

Los scripts de preparación y los verificadores local/Neon se actualizaron en
conjunto. Para un rol existente, el incremento está en
`docs/database/update_admin_writer_sync_status.sql`: requiere revisión 0006,
operador/destino autorizados y la marca de sesión
`mitbol.approved_admin_write=hu010-status-reviewed`. En cloud exige también la
comprobación externa de TLS. No se ha ejecutado en mitbol_dev o Neon.

Para una publicación futura, cerrar los gates administrativos y coordinar
backend y ACL en una misma ventana: el verificador anterior rechaza los nuevos
grants y el perfil nuevo exige esas dos columnas. Verificar después desde un
LOGIN nuevo antes de reactivar. La planificación del despliegue se deja al
encargado; este bloque solo ejecuta pruebas en PostgreSQL temporal.

Verificación del GET y sus permisos: **201 pruebas aprobadas** en 112.59
segundos sobre PostgreSQL 18 temporal, sin fallos u omisiones. Incluye consultas,
esquemas, HTTP aislado, login real Admin/Staff, rechazo de columnas y DML no
autorizados, aplicación del incremento, error 503 sin permisos y regresiones
de los perfiles de escritor, descuentos y destacados. Se observaron los dos
avisos de deprecación existentes de Starlette/TestClient. No se ejecutó la
suite completa del repositorio en este bloque ni se contactó a Loyverse.

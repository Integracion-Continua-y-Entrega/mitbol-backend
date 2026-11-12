# HU-010 — Administración de sincronizaciones

Estado: constructor, esquema y servicio del resumen implementados y probados. Rama
`feature/hu-010-sync-management`, creada desde develop `7d9b1c6`, que integra HU-009.
No se han activado nuevos endpoints o procesos ni se han modificado permisos.

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
y sesión y fallo ante una entidad ausente. No se modificaron ACL o rutas.

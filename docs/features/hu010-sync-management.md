# HU-010 — Administración de sincronizaciones

Estado: alcance backend permitido implementado; historial persistente pendiente. Rama
`feature/hu-010-sync-management`, creada desde develop `7d9b1c6`, que integra HU-009.
Se añadieron GET de estado, POST manual, worker privado y entrada periódica.
La ejecución real sigue deshabilitada sin configuración explícita. No se han
activado procesos, programadores o despliegues en infraestructura real.

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

No hay un planificador periódico activo en este repositorio. La entrada
`app.cli.run_sync` permite que un programador externo invoque una operación y
omita el intento si el destino está ocupado. Configurar el horario y desplegar
los procesos queda a cargo del encargado de infraestructura.

## Bloques de implementación

| Bloque | Resultado verificable |
|---|---|
| 1 | Consulta agregada por entidad: total de filas, filas con fecha y última fecha observada. |
| 2 | Esquemas, servicio y GET protegido; diferencias entre tabla vacía, fechas ausentes y datos conocidos. |
| 3 | Coordinación de ejecución, observación de actividad y rechazo de simultaneidad, sin inventar historial. |
| 4 | Operación manual autorizada que reutilice los servicios y conserve la separación entre rol web y operador. |
| 5 | Entrada periódica reutilizable, errores seguros y pruebas de convivencia con solicitudes manuales. |
| 6 | Regresiones, contrato para frontend, documentación y commits por bloque funcional. |

La solicitud web delega por HTTP autenticado a un worker loopback independiente.
No se concede DML comercial al rol web ni se exponen credenciales de Loyverse a
React. El worker tiene configuración, pool y proceso operativos propios. Sin
URL/token de delegación, el POST devuelve 503. El router legado sigue deshabilitado.

Permisos: `catalog:read` para consultar estado y `settings:write` para solicitar
ejecuciones (Admin, no Staff), además de Origin y CSRF. Reutiliza los
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

La respuesta contiene `entities`, con cinco resúmenes, `execution_active` y
`history_available=false`. Cada entidad incluye nombre, total_count,
synced_count y last_synced_at (UTC o null). Incluso una base vacía devuelve
cinco resúmenes. No expone éxito, última ejecución o historial.
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

## Actividad de ejecución

`execution_active` observa una marca transaccional exclusiva de los servicios
actualizados de catálogo y descuentos. Se adquiere después de verificar el
destino y obtener el bloqueo comercial, antes de leer Loyverse; se libera al
finalizar la transacción, con commit o rollback. No crea tablas ni conserva
resultados. Las ediciones locales no emiten esta marca.

La consulta de `pg_locks` se limita a la clave y base actuales, sin adquirir
bloqueos o ampliar permisos del rol web. Se distingue la clave bigint de la
variante PostgreSQL de dos enteros, conforme a la
[referencia de PostgreSQL 18](https://www.postgresql.org/docs/18/view-pg-locks.html).

La actividad es una observación en el momento de consultar. Puede cambiar
inmediatamente después; no sustituye el bloqueo al ejecutar una solicitud.
Procesos antiguos que no emitan la marca tampoco pueden identificarse mediante
ella. Los conteos/fechas y la actividad se consultan por separado: no representan
un resultado o historial de la misma ejecución.

Verificación de actividad: **167 pruebas aprobadas** en 31.28 segundos sobre
PostgreSQL 18 temporal, sin fallos u omisiones. Se comprobaron emisión antes
del proveedor, commit/rollback, concurrencia entre ambos servicios, marca
ocupada sin liberar bloqueos ajenos, aislamiento por base y tipo de clave,
lectura web con operador independiente y regresiones de catálogo/descuentos.
Los dos avisos de deprecación existentes de Starlette/TestClient continúan.
No se habilitó una solicitud manual o un planificador periódico.

## Solicitud manual

El cuerpo `ManualSyncRequest` requiere target, limitado a `catalog` o `discounts`.
No acepta `all`, valores por defecto, credenciales, base, tienda o identidad
enviadas por el cliente. Se aprobaron **35 pruebas** de este contrato y los
esquemas de estado en 0.17 segundos, sin abrir PostgreSQL.

`ManualSyncCoordinator` recibe callbacks asíncronos
configurados por el servidor para cada proceso y falla si no hay uno disponible.
No reutiliza la sesión del rol web ni busca credenciales implícitas. Espera el
callback elegido y propaga su resultado; no reintenta o usa el otro proceso.

`POST /api/admin/sync/requests` acepta `{"target":"catalog"}` o
`{"target":"discounts"}`. Solo existe con cuentas administrativas y catálogo
habilitados. La clave local legada no habilita esta operación. La autorización
revalida permiso, sesión, Origin y CSRF en una transacción corta que termina
antes de esperar al worker, para permitir consultar estado desde la misma cuenta.
El evento `sync.manual.authorize` registra esa autorización en AuditEvents;
no acredita finalización ni sustituye un historial completo de ejecuciones.

Se devuelve 200 y status=completed únicamente tras recibir y validar el resultado
del worker. El resultado contiene conteos estrictos, no el payload Loyverse.
No hay cola durable ni aceptación 202. El estado/permiso se comprueba al autorizar
la operación; una revocación posterior no deshace una transacción ya ejecutada.

Errores: 401/403 de acceso, 422 con mensaje genérico ante un cuerpo inválido,
409 si el destino está ocupado, 502 por datos/comunicación con el proveedor y
503 por configuración o resultado no confirmado. Un timeout puede ocurrir
después del commit: comprobar estado antes de repetir. No hay reintentos de POST
o redirecciones del cliente del worker.

## Ejecución operativa y periódica

`SyncWorkerRuntime` comparte preflight, servicios y bloqueos entre el worker
HTTP y `python -m app.cli.run_sync`. Rechaza los roles web como operador y
requiere destino/base/tienda explícitos. Local admite mitbol_dev y
mitbol_auth_local; las bases efímeras se limitan a los patrones del laboratorio.
Cloud mantiene las comprobaciones TLS y de destino existentes. No carga .env.

El comando es preflight por defecto. `--execute` autoriza una operación;
`--skip-if-busy` permite a un programador externo omitirla con código 0 sin
descargar datos cuando otra operación tiene el bloqueo. No crea timers, tablas
o tareas de fondo al iniciar FastAPI. Catálogo y descuentos son operaciones
independientes; no se ofrece una transacción conjunta para target=all.

Configuración, separación de entornos y límites de publicación:
[worker y ejecución periódica](../deployment/hu010_worker.md).

## Estado de aceptación

El historial persistente y la revisión de frontend siguen pendientes. No marcar
HU-010 completamente Hecho mientras el criterio de historial siga sin soporte,
según las páginas 29–30 del backlog. No se ha desplegado, ejecutado una tarea
programada real o sincronizado una cuenta Loyverse real durante este trabajo.

## Verificación final del alcance permitido

El 12 de noviembre de 2026 se aprobaron **1,805 pruebas de la suite completa**,
sin fallos, errores u omisiones, en 654.72 segundos sobre Windows, Python 3.13
y PostgreSQL 18. Se observaron dos avisos de deprecación de Starlette/TestClient.
El comando equivalente al usado por CI es:

```text
python -B scripts/test_auth_phase1.py --with-reader -ra --junitxml=test-results/pytest.xml
```

La prueba integrada recorre sesión real Admin, rol web limitado, delegación
HTTP ASGI, worker, pool operativo y proveedor HTTP simulado. Comprueba commit
real, rechazo de Staff/CSRF/origen/cuerpos inválidos, conservación de datos ante
fallos, consulta de estado durante la ejecución desde la misma cuenta, rechazo
de otra solicitud manual y omisión de un disparo periódico ocupado. El worker
no usa la sesión web para sincronizar y no hay fallback al pool público.

También se verificaron valores de configuración, cuerpos/resultados estrictos,
errores sin secretos, límites del worker, ausencia de redirecciones/reintentos
del POST y regresiones del catálogo, descuentos, permisos y autenticación.
El laboratorio elimina del proceso hijo variables heredadas SYNC_ y LOYVERSE_
para no utilizar accidentalmente un ejecutor o credenciales reales.

No se probó un worker por TCP en infraestructura real, un proveedor real o una
pantalla React; esas comprobaciones y el check remoto de CI son independientes
de esta evidencia local. Los informes XML y guías privadas no se versionan.

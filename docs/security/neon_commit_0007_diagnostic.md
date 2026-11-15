# Diagnóstico del COMMIT de 0006/0007 en Neon

Fecha: 14 de noviembre de 2026. Estado: causa probable identificada; causa exacta pendiente del diagnóstico del proveedor. No se autoriza ni ejecuta un nuevo intento con este documento.

El fallo probablemente corresponde al aprovisionamiento del trabajador con un verificador SCRAM generado en el cliente, incompatible con la gestión de contraseñas de Neon. Las migraciones y los permisos no presentaron errores antes del COMMIT. No hay evidencia de un defecto en sus definiciones que justifique cambiarlas.

## Evidencia del ensayo original

- Proyecto: `delicate-glade-05265146` (mitbol-dev).
- Rama autorizada: `test-migrations-0007`, ID `br-winter-mouse-bdoeizoh`, hija de `production`.
- Endpoint directo: `ep-lingering-wildflower-bd1t2n4a.c-8.us-east-2.aws.neon.tech`.
- Base: `mitbol_cloud_dev`; operador: `neondb_owner`; servidor: PostgreSQL `18.7 (a21afb5)`.
- Referencia temporal aproximada: 14/11/2026, 12:34 de Ciudad de México / 18:34 UTC. Se deriva de la modificación del archivo privado de credencial a las 18:34:15 UTC, ANTES del COMMIT; no es el timestamp exacto del error.
- SQLSTATE conservado: `XX000`; fase: `commit_authorized_temporary_branch_only`; COMMIT no confirmado.
- Una conexión posterior READ ONLY confirmó revisión `20261106_0005`, ausencia de `Discounts`, `SyncRuns` y `mitbol_catalog_worker`, y las 18 tablas / 119 filas originales con SHA-256 coincidentes.
- El administrador y lector habían autenticado mediante LOGIN real al endpoint temporal. Sus contraseñas no se cambiaron.
- La credencial inicial del trabajador quedó en un archivo privado; no se incluye aquí y no es utilizable mientras el rol no exista.

Secuencia dentro de una sola transacción:

1. Verificar host, base, IDs de proyecto/rama/endpoint, TLS verify-full y channel binding; adquirir bloqueo asesor de transacción.
2. Aplicar `20261111_0006`: creación de Discounts; verificar revisión y tabla vacía.
3. Aplicar `20261114_0007`: creación de SyncRuns; verificar revisión y tabla vacía.
4. Comparar esquema con SQLAlchemy: cero diferencias; datos y secuencias anteriores conservados.
5. Aplicar los cuatro scripts de permisos enumerados abajo. Sus límites BEGIN/COMMIT se sustituyeron por la transacción exterior, conservando sus sentencias y guards.
6. Generar un verificador SCRAM mediante `psycopg2.extensions.encrypt_password(..., algorithm="scram-sha-256")` y suministrarlo a ALTER ROLE del trabajador.
7. Comprobar 472 privilegios de columna por rol: administrador y trabajador coincidentes con sus perfiles; lector sin escrituras.
8. Fallar al confirmar el COMMIT. Se detuvieron las escrituras y se verificó el rollback mediante una nueva conexión.

El ejecutor ad hoc eligió el verificador generado en el cliente para evitar enviar una contraseña sin hash al SQL. Esa elección pasó por alto una restricción de Neon que ya figuraba en la sección 7.4 de [admin_auth_phase5.md](admin_auth_phase5.md). El código de la aplicación y las migraciones no generan ese verificador: fue una decisión del ejecutor, no de HU-008/HU-010.

## Qué se conserva y qué falta del error

Se conservaron SQLSTATE, fase, tipo de excepción, operaciones, resultados previos al COMMIT y comprobación posterior del estado. El manejador anterior redujo la excepción a un diagnóstico seguro: NO conservó MESSAGE_PRIMARY, DETAIL, HINT, CONTEXT ni ubicación C. Por ello no puede reconstruirse el error completo con los logs del cliente disponibles.

En una inspección nueva READ ONLY se confirmó:

- `neon.forward_ddl = on`.
- Plano de control configurado (sólo se consultó un booleano; no se expuso su URL).
- Biblioteca `neon` precargada.
- El operador no tiene `pg_read_server_files`. No se intentó leer archivos del servidor ni elevar permisos.
- La rama sigue en 0005 y los nuevos objetos no existen.

Neon documenta que los passwords SQL deben suministrarse sin prehash y que se cifran al almacenarse: [Manage roles](https://neon.com/docs/manage/roles). Su código público `NeonXactCallback` llama `SendDeltasToControlPlane` en PRE_COMMIT; las altas y cambios de contraseña se incluyen en esos deltas. Errores HTTP o de comunicación en esa ruta pueden producir ERROR: [neon_ddl_handler.c](https://github.com/neondatabase/neon/blob/main/pgxn/neon/neon_ddl_handler.c).

Esto explica el momento del fallo y es coherente con XX000, pero no demuestra el mensaje HTTP o rama de código exactos que fallaron en el build desplegado. Un problema transitorio del plano de control sigue siendo una alternativa hasta obtener el diagnóstico original. El código público main tampoco acredita identidad byte por byte con el build del servidor.

## Pruebas mínimas ejecutadas, sólo PostgreSQL local

PostgreSQL 18.2 Windows, con `scripts/test_auth_phase1.py`, clúster nuevo y bases temporales propias. Todos los datos fueron sintéticos. No se usaron el respaldo real, credenciales privadas ni API externas.

| Caso | Resultado después de COMMIT y conexión nueva |
| --- | --- |
| 0006 + 0007 | Correcto |
| Migraciones + ACL administrativas | Correcto |
| Lo anterior + trabajador con PASSWORD NULL y ACL mínimas | Correcto |
| Lo anterior + trabajador con verificador SCRAM cliente | Correcto |
| Lo anterior + trabajador con contraseña SQL sintética | Correcto |
| Sólo creación del trabajador + verificador SCRAM, sin migraciones | Correcto |

Resultado final: **6 passed**, cero fallos, 2 advertencias de deprecación ajenas al diagnóstico; datos y secuencias anteriores preservados. Los esquemas migrados coinciden con los modelos y se ejecutaron los verificadores locales correspondientes de administrador/trabajador. No se ejecutaron downgrades.

La primera corrida no llegó a los escenarios por una fecha `synced_at` ausente en la fixture sintética. Se corrigió únicamente ese archivo temporal; la corrida final pasó completa. No fue un error de las migraciones.

Límite: el harness local utiliza autenticación trust en loopback dentro de su clúster desechable. Estos resultados prueban COMMIT, almacenamiento SCRAM y ACL en PostgreSQL estándar; no prueban autenticación SCRAM de Neon ni sus hooks de control. Los dos clústeres locales se detuvieron.

Artefactos temporales locales:

- Prueba: `C:\Users\ferch\AppData\Local\Temp\mitbol-neon-commit-diagnostic-5ae62d8t\test_commit_matrix.py`.
- JUnit final: `C:\Users\ferch\AppData\Local\Temp\mitbol-neon-commit-diagnostic-5ae62d8t\results.xml`.
- Log final del harness: `C:\\Users\\ferch\\AppData\\Local\\Temp\\mitbol-sync-preflight-cf8f7e76c2064954a2cfc9be1e159afa\\test-run.log`.

## Corrección recomendada y siguiente ensayo, pendiente de autorización

Conservar las migraciones y los grants. Corregir la estrategia del ejecutor ad hoc:

1. Reconfirmar los IDs de la rama temporal y 0005. Mantener production excluida.
2. Aplicar 0006 y 0007 en una transacción que NO cree roles ni cambie passwords. Confirmar y verificar mediante conexión nueva.
3. Aplicar y verificar las ACL administrativas como paso separado.
4. Preparar el trabajador con el SQL revisado y verificar que el COMMIT de creación/grants funciona antes del aprovisionamiento de contraseña.
5. Inicializar su contraseña únicamente por un mecanismo admitido por Neon, sin verificador SCRAM generado por el cliente. No usar Console/API para CREAR el rol con membresía neon_superuser, ni desactivar los hooks del proveedor.
6. La vía SQL documentada requiere que la contraseña llegue al servidor sin prehash, dentro de TLS. psycopg2 adapta parámetros en el cliente: no garantiza ocultación en logs/observabilidad de Neon. Antes de esa escritura se necesita acordar un mecanismo compatible con la prohibición de exponer secretos, confirmar la redacción con el proveedor o seguir el procedimiento atendido de contraseña transitoria de la sección 7.4 existente. No se puede prometer ausencia de registros internos del proveedor.
7. Ejecutar los verificadores mediante LOGIN real y comparar nuevamente los datos. No lanzar sincronizaciones reales.

No es una corrección demostrada todavía en Neon. No se propone repetir el bloque completo ni cambiar globalmente password_encryption, autenticación o permisos. Capturar SQLSTATE, severidad, archivo/línea/función del servidor y momento de fallo en el próximo intento autorizado. MESSAGE_PRIMARY/DETAIL/HINT/CONTEXT se revisarán y redactarán ANTES de persistir o compartir; no imprimir raw exceptions, SQL con password, argumentos, DSN, hashes ni payloads internos. Si no puede garantizarse esa redacción, pedir el extracto saneado al proveedor.

## Texto preparado para soporte; NO enviado

> En el proyecto y endpoint indicados arriba, una transacción sobre una rama temporal aplicó 0006/0007 y ACL sin errores, y creó un rol SQL de privilegios mínimos con PASSWORD NULL. Después suministró un verificador SCRAM generado en el cliente a ALTER ROLE. El servidor devolvió XX000 únicamente al COMMIT, y una conexión posterior confirmó el rollback íntegro. Neon forward_ddl está habilitado. La documentación indica que no se admiten passwords prehasheados. En PostgreSQL 18 estándar los seis casos aislados, incluido éste, confirman correctamente.
>
> Solicitamos correlacionar los logs de compute/control plane del 14/11/2026 alrededor de 18:34 UTC para el endpoint indicado y facilitar MESSAGE_PRIMARY/DETAIL/HINT/CONTEXT saneados, ubicación del error, estado HTTP y request/correlation ID si existen. ¿Fue un rechazo del verificador prehasheado o un fallo de sincronización del plano de control? ¿Cuál es la vía admitida para inicializar una credencial de un rol SQL limitado sin transmitir un verificador cliente, sin otorgar neon_superuser y con redacción de secretos garantizada en registros administrados? Confirmar también que no quedó un estado de rol residual en el plano de control, aunque pg_roles no contenga el rol.

Adjuntar sólo este informe saneado y, si soporte los requiere, los hashes de scripts y resultados sintéticos. No adjuntar .env, dump, passwords, verificadores, cookies ni payloads de autenticación.

## Integridad de los archivos ejecutados

- `migrations/versions/20261111_0006_hu009_discounts.py`: `840a77d51851a5ba14faed9663ff0663f1fef2ab5ad1d6976e8b1ba41a31cad6`
- `migrations/versions/20261114_0007_hu010_sync_runs.py`: `afe3e1e2b7759dede7c6973274cbee8b3814f2360bdfb271b23080d777e8faf5`
- `docs/database/update_admin_writer_highlights.sql`: `9822600fa8c0757ad3f953ae6f02916e82c399ae6c35e3e658ea4d59aebac514`
- `docs/database/update_admin_writer_discounts.sql`: `7dcf66cc3867e4e5267fb6aaac19524a2498483f8e2a7d9197d6befcd007887b`
- `docs/database/update_admin_writer_sync.sql`: `7de64563844185523ffba3387bdc6f3b67b809799636ff24fe683ce5db815816`
- `docs/database/prepare_catalog_worker.sql`: `7c6b71d377cc8ec4b2c91cae8fa2d0273a47f5d4c07c5cd5ddb64f8bd3684445`

Durante este diagnóstico no se escribieron cambios en Neon, production, Render, R2 ni Loyverse. No se modificaron archivos existentes del proyecto ni se hicieron commits/push.

## Seguimiento autorizado: migraciones y permisos separados

Este apartado registra ensayos posteriores al diagnóstico anterior. No autoriza
repetir scripts ni escribir en production.

- Se reconfirmaron las capturas originales y los IDs de proyecto, rama y endpoint
  mediante conexiones directas nuevas, con TLS verify-full y channel binding.
  Todas las conexiones de este seguimiento corresponden a test-migrations-0007.
- 0006 y 0007 se confirmaron por separado, sin roles ni contraseñas, a las
  19:12:27 y 19:12:44 UTC del 14/11/2026. Conexiones nuevas verificaron ambas
  tablas y la revisión final 20261114_0007.
- Los tres scripts administrativos de la lista anterior se ejecutaron después
  en una única transacción, conservando guards y sentencias. COMMIT confirmado
  a las 19:23:15 UTC. Se concedieron UPDATE de is_featured/is_new, lectura de
  Discounts y SyncRuns, INSERT limitado de solicitudes y las columnas de lectura
  adicionales previstas por HU-010. No se modificaron contraseñas existentes.
- prepare_catalog_worker.sql se confirmó a las 19:25:32 UTC en otra transacción.
  El rol existe con LOGIN y PASSWORD NULL: **no tiene credencial provisionada**.
  No se envió contraseña ni verificador SCRAM. El archivo privado anterior
  no se actualizó ni se utilizó para intentar autenticar al trabajador.
- El trabajador no tiene superusuario, CREATEDB, CREATEROLE, INHERIT,
  REPLICATION, BYPASSRLS, DDL, grant options ni membresías hacia otros roles.
  PostgreSQL registró a neondb_owner como administrador del nuevo rol
  (grantor cloud_admin; inherit_option=false, set_option=false). Esa relación
  no otorga privilegios de operador al trabajador.
- Desde el operador se comprobaron las 118 columnas y sus cuatro operaciones
  contra la matriz del verificador existente: cero diferencias. También se
  verificó USAGE exclusivamente en las cuatro secuencias comerciales previstas,
  sin SELECT/UPDATE de secuencias. Fotografías, etiquetas y campos locales
  protegidos carecen de permisos de modificación para el trabajador.
  **Esto no sustituye su verificador mediante LOGIN real**, que sigue pendiente.
- Los verificadores SQL completos de administrador y lector pasaron con LOGIN
  nuevos, incluyendo una ejecución posterior a crear el trabajador. El
  administrador pasó además planes EXPLAIN de UPDATE de destacados/novedades
  e INSERT de solicitud, y cinco rechazos esperados con SQLSTATE 42501
  (nombre comercial, precio, estado de solicitud y DELETE de solicitudes/productos).
  EXPLAIN no ejecutó esos cambios ni dejó solicitudes en cola.
- Los 119 registros de las 20 tablas conservaron sus SHA-256; esquema y valores
  de secuencias permanecieron iguales. Discounts y SyncRuns siguen vacías,
  alembic_version sigue en 20261114_0007 y las ACL del lector no cambiaron.
  No se realizaron sincronizaciones comerciales ni conexiones a production.

### Dos incidencias del ejecutor de comprobación

La comparación ad hoc de ACL produjo inicialmente un falso positivo: usaba
acldefault('S', owner) para secuencias, pero S representa FOREIGN SERVER y la
secuencia requiere s minúscula, según la
[documentación de PostgreSQL](https://www.postgresql.org/docs/18/functions-info.html).
Al materializar las ACL de las cuatro secuencias, aparecieron ocho entradas
SELECT/UPDATE del propietario que el comparador había omitido cuando relacl
era NULL. Excluir exactamente esas ocho entradas reprodujo la huella anterior
del resto de ACL, sin otras diferencias. No cambió ningún permiso efectivo del
propietario; se corrigió el criterio de auditoría, no el SQL de MITBOL.

Después de pasar el verificador completo del lector y su SELECT de catálogo,
una prueba EXPLAIN adicional se detuvo con 25P01 al intentar SAVEPOINT:
verify_render_reader.sql termina con ROLLBACK y el ejecutor no abrió otra
transacción explícita antes de esa prueba. No se repitió la operación ni se
hicieron nuevas escrituras. Las pruebas negativas adicionales del lector
quedaron pendientes; su verificador completo de ACL sí pasó. No hubo XX000
en este seguimiento.

### Pendiente: credencial segura y LOGIN del trabajador

Se detuvo el aprovisionamiento de contraseña porque no se ha acreditado la
redacción de secretos en registros administrados de Neon y no hay una sesión
Console/API autorizada disponible para el ejecutor. No utilizar el password
anterior ni introducir una contraseña literal en SQL Editor, terminal o informe.

Intervención manual propuesta: en mitbol-dev, seleccionar explícitamente
test-migrations-0007, abrir Postgres database → Roles → mitbol_catalog_worker →
Reset password. Guardar la conexión directa resultante únicamente en el archivo
privado neon-test-worker.env, verificando host temporal y base mitbol_cloud_dev.
No usar Add role: los roles creados por Console/API reciben neon_superuser,
incompatible con los mínimos requeridos
([Neon: roles](https://neon.com/docs/manage/roles)).

La sección 7.4 de admin_auth_phase5.md registra que otro rol con PASSWORD NULL
rechazó Reset password con cannot update password for role without password.
Si ocurre, detenerse y pedir a soporte de Neon un mecanismo de inicialización
del rol SQL existente que conserve privilegios mínimos y proteja los registros.
No repetir el workaround SQL de contraseña transitoria sin resolver esa condición.
No se garantiza que Console pueda inicializar este rol en este proyecto.

Una vez disponible una credencial válida, reconfirmar rama/host/base, realizar
LOGIN nuevo del trabajador, ejecutar verify_catalog_worker.sql y terminar las
pruebas de autorización pendientes con transacciones explícitas. Hasta entonces
la validación del trabajador está incompleta y no procede certificar su
aprovisionamiento para production. Las migraciones no requieren repetirse.

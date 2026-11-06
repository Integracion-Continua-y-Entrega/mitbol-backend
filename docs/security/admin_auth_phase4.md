# MITBOL — Autenticación administrativa, fase 4

Fecha: 6 de noviembre de 2026, America/Mexico_City. Rama: `design/admin-auth-security`.

Este documento conserva el cierre de fase 4. La [fase 5](admin_auth_phase5.md) añade dos tablas de límites, el head 0005 y sus grants/verificación en los scripts actuales; también incluye aprovisionamiento, gates y el procedimiento vigente. La matriz de fase 4 describe las operaciones previas de identidad/catálogo.

Se diseña y valida **un login PostgreSQL web con permisos por columna**, separado del lector y del operador de migraciones. El laboratorio ejecuta los servicios reales de fases 2/3 con ese login limitado. No crea un escritor en Neon, no modifica Render ni abre administración cloud. No cambia modelos, migraciones, frontend, reglas HU-008 ni sincroniza Loyverse. Los roles de esta fase existen exclusivamente durante las pruebas en PostgreSQL 18 temporal.

El esquema conserva las ocho tablas del catálogo y las siete de identidad de `20261105_0004`. La [fase 3](admin_auth_phase3.md) conserva los contratos HTTP y permisos Admin/Staff; esta fase verifica los privilegios del proceso que los ejecuta.

## 1. Resultado del análisis y arquitectura elegida

Se revisaron [auth_service.py](../../app/services/auth_service.py), [repositorios](../../app/repositories/auth.py), [servicios HU-005](../../app/services/product_enrichment_service.py), [HU-007](../../app/services/product_availability_service.py), [auditoría/unidad transaccional](../../app/services/admin_transaction.py), modelos, revisiones Alembic, conexiones, guards, menú/medios y los scripts/pruebas del lector.

La identidad propuesta `mitbol_admin_writer` corresponde al **proceso web**, no a cada administrador. No tiene ownership, membresías, SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS ni grant option. Usa login propio; no se implementa mediante SET ROLE sobre el pool público. Las ACL de aplicación son explícitas, sin GRANT ALL ni permisos de escritura sobre tablas comerciales completas.

Se separan responsabilidades:

| Identidad | Función | Fuera de su alcance |
| --- | --- | --- |
| `mitbol_render_reader` | Ocho tablas públicas del menú, exclusivamente SELECT; pool público. | Identidad, hashes, sesiones, auditoría, secuencias y escritura. |
| `mitbol_admin_writer` | Autenticación/sesiones y las nueve escrituras administrativas actuales; pool administrativo. | Altas de personas/cuentas/empleados, asignar roles, sincronizar comercio, retención, DDL y migraciones. |
| Operador controlado | Migraciones, aprovisionamiento, gestión de acceso y mantenimiento con procedimientos revisados. | Su credencial no se configura en el servicio web. No se crea un nuevo operador en esta fase. |

«Administrar cuentas» significa aquí las operaciones ya implementadas: actualizar último login, cambiar contraseña y versionar/revocar acceso. No existe un endpoint de altas, activación, cambio de correo o gestión de roles que justifique dar esos permisos al escritor. El aprovisionamiento futuro exige un diseño aparte; no se anticipan privilegios.

### Ajuste del repositorio para permisos mínimos

`employee_and_roles()` hacía FOR UPDATE sobre Employees, Roles y EmployeeRoles. PostgreSQL exige UPDATE al menos en una columna de cada tabla bloqueada; en EmployeeRoles eso permitiría cambiar una asignación de seguridad. Cambiar a FOR SHARE tampoco elimina ese requisito. Véase [SELECT y locking](https://www.postgresql.org/docs/18/sql-select.html).

Se evaluaron tres alternativas: conceder UPDATE de seguridad; conservar los locks mediante una función privilegiada; reutilizar el protocolo existente de cuenta/auth_version. Se eligió la tercera: `employee_and_roles()` ahora consulta sin bloquear esas tablas. Se conservan FOR UPDATE de PersonalAccounts y AccountSessions, donde ya hay actualizaciones legítimas.

No se cambia el modelo de identidad ni se introduce otra autoridad de permisos. **Toda herramienta autorizada que modifique el acceso de una persona debe bloquear primero su cuenta, modificar empleado/asignaciones, incrementar auth_version y revocar sesiones en la misma transacción.** Login y las operaciones autenticadas ya bloquean esa cuenta antes de leer el acceso. Las pruebas hacen competir una modificación con una revocación y una solicitud pendiente con un cambio de asignaciones.

Roles.code y la política ROLE_PERMISSIONS no se editan desde el proceso web. Un cambio global del catálogo/política de roles requiere mantenimiento coordinado e invalidar las sesiones afectadas. SQL manual de operador que ignore el protocolo no queda protegido por el lock de cuenta; los cambios directos de las pruebas son sintéticos. El retiro de locks sobre roles también elimina la serialización global entre cuentas distintas que compartían un rol.

La función SQL privilegiada queda como alternativa si posteriormente se exige encapsular operaciones en la base. Hoy añadiría ownership, EXECUTE, search_path y mantenimiento de funciones sin necesidad para las operaciones existentes. GRANT por columna resulta suficiente para limitar los datos comerciales que puede modificar esta identidad web.

## 2. Auditoría de SQL por operación

Las consultas ORM cargan columnas que no siempre aparecen en la respuesta HTTP. La matriz de lectura considera el SQL real, no sólo los DTO. No se registran parámetros, hashes, contraseñas o tokens durante la observación de consultas.

Lecturas comunes de autenticación: PersonalAccounts (cuenta bajo FOR UPDATE), Employees, EmployeeRoles y Roles (habilitación/permisos), Persons.name buscando person_id, AccountSessions (token y sesión bajo FOR UPDATE). Las comprobaciones de conexión leen identidad y catálogos de PostgreSQL. No requieren tablas comerciales adicionales.

| Operación real | Lecturas además de autenticación | Escrituras exactas | Secuencia / relación |
| --- | --- | --- | --- |
| Login admitido | Cuenta por email; sesión antigua de la misma cuenta | PersonalAccounts.last_login_at; INSERT AccountSessions; UPDATE revoked_at/revocation_reason de la sesión anterior; INSERT AuditEvents | Sesión UUID del backend, FK account_id. Sólo secuencia AuditEvents. |
| Login denegado | Cuenta/empleado/roles cuando existen | INSERT AuditEvents con actor anónimo | Sólo secuencia AuditEvents; no inserta sesión. |
| GET me / csrf / petición autenticada | Sesión por hash y contexto vigente | AccountSessions.last_activity_at, idle_expires_at cuando corresponde | Sin secuencia. Lecturas administrativas usan la misma unidad autenticada. |
| Logout | Sesión vigente | AccountSessions.revoked_at, revocation_reason; INSERT AuditEvents | FK de auditoría a cuenta. |
| Logout-all | Sesiones no revocadas de la cuenta | PersonalAccounts.auth_version; revocación de sesiones; INSERT AuditEvents | Sin DELETE ni limpieza de sesiones. |
| Change-password | Hash actual y acceso | PersonalAccounts.password_hash, updated_at, auth_version; revocación de sesiones; INSERT AuditEvents | No actualiza email, person_id o is_active. |
| Acceso denegado / invalidación observada | Cuenta y sesión afectada | Revocación si procede; INSERT AuditEvents | Unidad original revierte; denegación se registra en otra transacción. |
| GET descripción / PATCH descripción | Products por PK | PATCH: Products.description; INSERT AuditEvents | No necesita secuencia de Products. |
| GET/PATCH disponibilidad de producto | Products y ProductVariants (selectinload) | PATCH: Products.is_available; INSERT AuditEvents | Lock transaccional de catálogo; no secuencia comercial. |
| GET/PATCH disponibilidad de variante | ProductVariants y Products (joinedload) | PATCH: ProductVariants.is_available; INSERT AuditEvents | Mismo lock transaccional de catálogo. |
| GET etiquetas / etiquetas de variante | Tags; ProductTags y ProductVariants para validar variante | Ninguna, salvo actividad de sesión | No necesita REFERENCES ni secuencia en lectura. |
| POST etiqueta | Tags / índice UNIQUE name | INSERT Tags.name; INSERT AuditEvents | Tags_tag_id_seq y AuditEvents_audit_event_id_seq. |
| PUT asociación | ProductVariants y Tags; clave única ProductTags | INSERT ProductTags(product_variant_id, tag_id) ON CONFLICT DO NOTHING; INSERT AuditEvents | FK a variante y etiqueta; sin secuencia de asociación. |
| DELETE asociación | Variante, etiqueta y asociación | DELETE ProductTags; INSERT AuditEvents | Elimina una asociación, no la etiqueta global. |
| GET imágenes | ProductVariants y ProductImages ordenadas | Ninguna, salvo actividad de sesión | Sin secuencia en lectura. |
| POST imagen | Variante con lock, ProductImages para máximo orden/recuperación | INSERT ProductImages(product_variant_id, image_url, display_order); INSERT AuditEvents | ProductImages_product_image_id_seq y AuditEvents_audit_event_id_seq. |
| PATCH orden de imagen | Variante e imagen perteneciente a ella, con lock | ProductImages.display_order; INSERT AuditEvents | No puede reasignar variante ni cambiar la URL. |
| DELETE imagen | Variante/imagen, conteo de referencias compartidas | DELETE ProductImages; INSERT AuditEvents | FK existente; diario de archivos fuera de PostgreSQL. |
| Recuperación de diario | ProductImages.image_url/product_image_id | Sin DML de catálogo; sólo recupera archivos | Transacción nueva y mismo advisory lock de imágenes. |
| Menú/listado/detalle/medios públicos | Ocho tablas del catálogo mediante el lector | Ninguna | Nunca usa el pool administrativo, incluso con cookie. |

Todas las escrituras de catálogo añaden el evento de éxito en la **misma transacción**. Las operaciones idempotentes confirmadas también generan evento. Los errores de esquema/Origin/cookie inexistente no fuerzan auditoría o consultas indiscriminadas. No se incorporan SELECT/INSERT sobre alembic_version al escritor.

## 3. Matriz exacta de privilegios del escritor

`—` significa denegado. INSERT/UPDATE se conceden por columna. DELETE es un privilegio de tabla en PostgreSQL y sólo se concede sobre las dos tablas de referencias indicadas. No hay TRUNCATE, REFERENCES, TRIGGER, MAINTAIN ni grant option.

| Tabla | SELECT concedido | INSERT | UPDATE | DELETE |
| --- | --- | --- | --- | --- |
| Products | product_id, loyverse_id, name, description, is_available, is_active, is_featured, is_new, synced_at | — | description, is_available | — |
| ProductVariants | product_variant_id, loyverse_id, name, price, is_available, is_commercially_available, synced_at, product_id | — | is_available | — |
| ProductImages | product_image_id, product_variant_id, image_url, display_order, created_at | product_variant_id, image_url, display_order | display_order | Sí |
| Tags | tag_id, name, created_at | name | — | — |
| ProductTags | product_variant_id, tag_id | product_variant_id, tag_id | — | Sí |
| ProductModifiers | — | — | — | — |
| ProductComplements | — | — | — | — |
| ProductModifierAssignments | — | — | — | — |
| Persons | person_id, name | — | — | — |
| PersonalAccounts | account_id, person_id, email, password_hash, is_active, auth_version, email_verified_at, last_login_at, created_at, updated_at | — | password_hash, auth_version, last_login_at, updated_at | — |
| Employees | person_id, employee_code, hired_at, is_active, created_at | — | — | — |
| Roles | role_id, code, name, description | — | — | — |
| EmployeeRoles | person_id, role_id | — | — | — |
| AccountSessions | session_id, account_id, token_hash, csrf_token_hash, auth_version, created_at, last_activity_at, expires_at, idle_expires_at, revoked_at, revocation_reason | Las once columnas de la fila, incluido UUID generado y campos nullable que emite el ORM | last_activity_at, idle_expires_at, revoked_at, revocation_reason | — |
| AuditEvents | audit_event_id, created_at | actor_account_id, action, outcome, resource_type, resource_id, request_id, failure_code | — | — |
| alembic_version y objetos futuros | — | — | — | — |

La lectura de price no permite modificarlo: el ORM carga la variante completa para disponibilidad. El escritor no necesita leer modificadores/complementos/asignaciones comerciales; sólo el lector los consulta. Persons.phone queda fuera. Las lecturas de atributos laborales completos y metadatos de rol reflejan las cargas ORM existentes; reducirlas con load_only sería una optimización posterior, no motivo para dar escrituras.

AuditEvents permite SELECT de id/fecha por el INSERT ... RETURNING del ORM. No permite leer el contenido de auditorías, actualizar, eliminar, fijar su PK o indicar created_at. Esa lectura limitada es compatible con auditoría append-only. Una futura pantalla de auditoría requerirá otra autorización y revisión SQL.

| Secuencia | Privilegio | Motivo |
| --- | --- | --- |
| Tags_tag_id_seq | USAGE | INSERT de etiqueta con PK SERIAL. |
| ProductImages_product_image_id_seq | USAGE | INSERT de referencia de imagen. |
| AuditEvents_audit_event_id_seq | USAGE | INSERT de evento. |
| Todas las demás | Ninguno | No se crean productos, variantes, personas, cuentas ni roles desde web. |

USAGE permite nextval/currval, no setval. UUID de sesiones y PK compuesta ProductTags no usan secuencia. Puede haber huecos tras rollback o llamadas a nextval: no es pérdida de integridad.

Las FK existentes las mantiene PostgreSQL sin conceder REFERENCES al ejecutor de DML. Ese privilegio se necesita para definir nuevas referencias, no para insertar filas sujetas a una FK ya creada. Las consultas previas conservan los 404 funcionales del servicio. Se prueban rechazos SQL de relaciones inexistentes, checks de auditoría y hashes de sesión.

Los permisos son aditivos: un UPDATE a nivel de tabla o vía PUBLIC/membresía anularía la protección por columnas. Véanse [GRANT](https://www.postgresql.org/docs/18/sql-grant.html), [privilegios y ownership](https://www.postgresql.org/docs/18/ddl-priv.html) y [membresías](https://www.postgresql.org/docs/18/role-membership.html). La verificación comprueba privilegios efectivos, no sólo ACL directas.

## 4. Scripts preparados y alcance

| Archivo | Uso |
| --- | --- |
| [prepare_admin_writer_local.sql](../database/prepare_admin_writer_local.sql) | Transacción de laboratorio: guarda de destino/operador/versión, rol nuevo, permisos explícitos, tres secuencias y timeouts. No contiene contraseña. |
| [verify_admin_writer_local.sql](../database/verify_admin_writer_local.sql) | Desde un login nuevo del escritor: identidad, atributos/membresías/ownership, acceso de esquema/base, todas las columnas efectivas, DELETE, secuencias, grant option, defaults peligrosos y rutinas propias accesibles. Sólo lee catálogos. |
| [test_admin_writer.py](../../tests/test_admin_writer.py) | Aplica scripts en la base temporal ya verificada, conecta con escritor/lector independientes, prueba SQL directo y servicios reales, limpia roles propios. |

La preparación rechaza cualquier base que no sea `mitbol_test_auth_<32 hex>`, operador distinto de mitbol_ci, conexión que no sea loopback, PostgreSQL distinto de 18, esquema fuera de 0004 o escritor existente. La fixture verifica además data_directory antes de ejecutar. No se usa SET ROLE para demostrar privilegios: current_user y session_user deben ser el login independiente esperado.

El verificador incluye relaciones ajenas a la lista y nuevas columnas: detecta permisos heredados de PUBLIC, grants amplios y deriva de ACL. Rechaza rutinas en esquemas de usuario ejecutables por el escritor; una extensión o función futura exigirá revisión. No define SECURITY DEFINER ni privilegios globales por defecto. Una reejecución no restablece silenciosamente un rol existente.

Los REVOKE de CREATE/TEMPORARY de PUBLIC se limitan a la base temporal y CREATE al esquema public; allí se repite la protección ya usada por el lector. En un entorno real afectarían a los demás usuarios que dependan de PUBLIC y requieren inventario/aprobación. No se ejecutan en Neon en esta fase.

La fixture aplica una **copia temporal** de los scripts existentes del lector adaptada al nombre/operador de prueba y a la revisión 0004; sus ocho SELECT y su política no cambian. Los archivos originales de preparación/verificación del lector permanecen intactos.

Los SQL de esta fase son deliberadamente de laboratorio. No deben convertirse en scripts de producción con una sustitución automática de strings: el destino, operador, extensiones, memberships, ACL de PUBLIC y dependencias reales deben revisarse antes.

## 5. Separación de conexiones FastAPI

La separación actual ya sirve para el laboratorio:

```text
GET /api/menu, /api/menu/{id}, /media/... -> get_db
    -> app.state.session_factory -> DATABASE_URL -> lector

/api/auth y /api/admin -> get_current_user / require_permission
    -> AuthService.transaction -> app.state.auth_session_factory
    -> AUTH_DATABASE_URL -> escritor
    -> sesión + catálogo permitido + AuditEvents -> un solo commit
```

El pool público se construye en `db/database.py`; el administrativo en `db/auth_database.py`. `get_authenticated_database()` entrega el db del contexto autenticado, sin fallback al público. No se mezcla una transacción del lector para validar y otra del escritor para modificar. `scope="function"` termina la unidad antes de responder; un fallo de auditoría no anuncia éxito.

En cloud, la conexión pública sigue verificando destino/TLS, identidad sin escritura y transacciones read-only. `READ_ONLY_ROLE_SQL` detecta también UPDATE por columna y uso de secuencias: el escritor propuesto falla esa prueba aunque no tenga UPDATE de tabla completa. La prueba usa ese chequeo con ambos logins. El lector mantiene su default_transaction_read_only y ausencia de permisos de identidad.

El laboratorio configura dos URLs a la misma base temporal con usuarios distintos y **sin overrides de get_db en la aplicación que se valida**. Un listener hace fallar la prueba si una consulta pública intenta usar el motor administrativo. Otra observación contrasta conexión y transacción del UPDATE de Products con el INSERT de AuditEvents. No captura valores enlazados.

No se habilita una conexión administrativa remota ahora. Antes de cloud harán falta validación TLS/destino e identidad del escritor, comprobación de su perfil de capacidades al conectar y manejo seguro de fallos, preservando el guard del lector. La URL administrativa no podrá sustituir DATABASE_URL; una ausencia/fallo administrativo deberá cerrar esas rutas, sin intentar operar como owner o lector.

Recomendación inicial para esa validación: conexión directa de Neon y pools pequeños separados; revisar presupuesto de conexiones por worker/instancia. Los advisory locks usados son de transacción. Si se opta por pooler, habrá que probar identidad, opciones de sesión y transacciones con su modo real; no asumir que persiste estado de sesión. Véase [pooling de Neon](https://neon.com/docs/connect/connection-pooling).

## 6. Pruebas y evidencia

Primera validación completa del archivo de pruebas del escritor: **100 aprobadas**, cero fallos/errores/omitidas, 29.40 s, dos deprecaciones existentes de Starlette/AnyIO. Evidencia: `TEMP/mitbol-sync-preflight-c528272060dd44088fbc72faabb5618d/test-run.log`. Después se agregaron tres casos de Staff e integridad de sesiones para la suite completa.

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra --maxfail=1 --tb=short
```

El ejecutor histórico crea dos clústeres desechables PG18, sanea variables heredadas y no lee .env. En la base auth exclusiva crea sólo roles de laboratorio, aplica la migración allí y elimina sus roles/grants al terminar. El otro clúster conserva las pruebas independientes del lector. Catálogo, cuentas y contraseñas son sintéticos. No conecta a Neon/mitbol_dev ni llama a Loyverse real.

Cobertura añadida:

- Login independiente de escritor/lector; verificación completa de ACL y lector existente; guarda de destino y reejecución; lector sin acceso a las siete tablas de seguridad.
- Nueve escrituras reales, descripciones, disponibilidad de ambos roles, etiquetas/imágenes con validaciones y conservación de campos comerciales.
- Login, renovación de actividad, logout, logout-all, cambio de contraseña y revocación con el escritor limitado.
- SQL negativo para precios, IDs/estados sincronizados, altas/bajas de catálogo, comercio, identidades, activación, roles, sesiones inmutables, contenido de auditoría, timestamps/IDs de eventos, secuencias no autorizadas y setval.
- CREATE TABLE/TEMP/SCHEMA/DATABASE/FUNCTION/INDEX, ALTER/DROP de tablas, CREATE ROLE/escalada/membresía/SET ROLE y modificación de versión Alembic rechazados por PostgreSQL. CREATE DATABASE se prueba fuera de transacción para no confundir un error de contexto con falta de privilegios.
- GRANT sin grant option se verifica por ausencia de efecto: PostgreSQL puede emitir WARNING en vez de excepción. El lector no recibe el permiso intentado.
- FK/checks, intento Alembic, inyección de grant peligroso directo y vía PUBLIC detectada, rollback de las nueve escrituras por fallo de auditoría, misma conexión/transacción, revocación/cambio de asignación concurrentes y separación de pools públicos.
- Se conserva toda la regresión previa, incluidas rutas públicas, cloud cerrado y reglas HU-008.

Resultado de la suite completa: **1210 pruebas aprobadas, cero fallos, cero errores y cero omitidas**, en 333.70 s, con las dos deprecaciones preexistentes de Starlette/AnyIO. Son las 1107 pruebas anteriores más 103 casos de fase 4. Ambos clústeres temporales quedaron detenidos y sus roles de laboratorio se limpiaron. `pip check` no detectó incompatibilidades; los enlaces locales y `git diff --check` verificaron correctamente.

Evidencia completa: `TEMP/mitbol-sync-preflight-e3e8c56888ce4f30896ddd2729a28c2b/test-run.log`. Las pruebas son PostgreSQL local y HTTP ASGI; no certifican Neon, Render, TLS remoto o navegador real. No se extrapola el resultado de un clúster PostgreSQL estándar a las memberships/extensiones/ACL concretas de Neon sin su revisión posterior.

## 7. Riesgos y decisiones pendientes

1. **El login SQL representa al backend.** Admin/Staff y el control de la cuenta afectada los comprueba FastAPI. El escritor puede operar sobre las filas admitidas por sus grants; las ACL no distinguen a las personas que usan React. No es una implementación de RLS ni aislamiento por usuario final.
2. Una credencial administrativa SQL comprometida podría alterar contraseñas/sesiones o insertar eventos atribuidos a cuentas existentes. Append-only evita actualizar/borrar eventos con esa identidad; no prueba por sí solo autoría criptográfica. Secretos, consultas parametrizadas, autorización del servidor y operación segura siguen siendo necesarios.
3. GRANT no obliga a acompañar cada UPDATE de un evento: la atomicidad se garantiza por la unidad de trabajo del backend y se prueba por fallos. Si se exige imponer esa relación incluso al SQL directo, o no entregar DML directo de cuentas al proceso, habría que diseñar funciones limitadas/otro servicio con ownership y permisos revisados antes de implementarlos.
4. Las restricciones de formato, normalización, pertenencia y límites HTTP siguen en los esquemas/servicios existentes. Las FK/UNIQUE/CHECK de base se preservan; las ACL no sustituyen esas validaciones. No se añade un máximo SQL de duración de sesión ni un trigger de auth_version en esta fase.
5. No se conceden permisos de retención/borrado de sesiones o auditoría a web. Tampoco UPDATE de nombre de etiqueta o URL de imagen, que los contratos actuales no implementan. Cualquier ampliación necesita actualizar matriz y pruebas negativas.
6. DDL de estructuras de aplicación y privilegios están bloqueados en la base validada. Eso no significa que se hayan prohibido todas las funciones incorporadas de PostgreSQL: rutinas de sistema, large objects, parámetros de sesión y acceso a otras bases mediante PUBLIC requieren inventario adicional en la rama real. No se revoca EXECUTE de funciones del sistema indiscriminadamente. La comprobación de ownership y rutinas propias no sustituye esa revisión. Los permisos en esta base no son un DENY en las demás bases de la rama.
7. La recuperación de imágenes sigue siendo un diario local, sin transacción distribuida ni persistencia cloud. No se compartirá raíz/diario entre bases diferentes. Antes de publicar, hace falta un almacenamiento persistente aprobado o mantener esas rutas desregistradas.
8. La futura herramienta de altas/roles debe implementar el protocolo de cuenta/versionado; no se implementó ahora ni se habilitó SQL manual sin coordinación. El ajuste de locks es válido bajo ese protocolo y con el catálogo de códigos de rol estable.

## 8. Procedimiento futuro para Neon y Render — NO EJECUTADO

### A. Preparación sin escrituras en infraestructura real

1. Revisar este diff/matriz, fijar versión de despliegue y aprobar alcance. Mantener AUTH_ENABLED=false y ADMIN_ENABLED=false en Render.
2. Preparar y revisar SQL offline incremental de `20261102_0003:20261105_0004`; comprobar siete tablas y dos roles de aplicación, sin cuentas predeterminadas ni cambios comerciales. Ensayar en PostgreSQL desechable con el operador de migraciones y el escritor limitado.
3. Preparar inventario de ACL efectivas, defaults, ownership, miembros, secuencias, extensiones, funciones privilegiadas/PUBLIC y otras bases de la rama. Si posteriormente se consulta Neon para reunirlo, usar sólo lectura y la autorización correspondiente; esta fase no realizó esa consulta.
4. Preparar backup/recuperación y criterios de vuelta atrás. El downgrade elimina datos de identidad: no ejecutarlo como respuesta automática a un fallo después de crear cuentas/sesiones.
5. Resolver el limitador compartido/persistente y su prueba multiinstancia; dominios HTTPS definitivos; cookies Secure/HttpOnly/SameSite, CORS explícito, Origin/CSRF y proxies de confianza; aprovisionamiento y recuperación de cuentas; observabilidad sin secretos y rotación. Seguirán bloqueadas las rutas mientras falten estos controles.

### B. Pasos que SÍ escribirían en Neon — requieren autorización posterior

1. Con operador controlado y conexión directa TLS verificada a la rama/base autorizadas, confirmar estado 0003 y aplicar `alembic upgrade 20261105_0004`. Esto crea tablas/índices y los roles de aplicación Admin/Staff. No usar el escritor ni Render para ejecutar Alembic.
2. Verificar head/esquema y que el lector sigue viendo sólo sus ocho tablas. La migración no otorga SELECT de seguridad al lector; comprobar defaults antes y después.
3. Crear mediante **SQL revisado** un rol web nuevo con los atributos y grants acordados, sin owner/membresías ni privilegios amplios. No usar el alta administrativa predeterminada de Console/API/CLI: Neon documenta que esas modalidades pueden otorgar neon_superuser; para estos permisos específicos recomienda SQL. Véase [gestión de roles de Neon](https://neon.com/docs/manage/roles).
4. Configurar contraseña fuerte por un canal protegido, nunca en el archivo SQL, historial, repositorio o logs. Portar las ACL revisadas al destino real con guardas explícitas de base/host/operador; no ejecutar los scripts LOCAL de esta fase contra Neon. Revisar efectos de cualquier REVOKE de PUBLIC con los operadores existentes.
5. Abrir un **login independiente** como escritor y verificar current_user/session_user y capacidades efectivas. Ejecutar en un entorno remoto de ensayo autorizado la batería positiva/negativa sobre datos desechables y transacciones revertidas; esto también puede consumir secuencias y requiere autorización de escritura. Verificación de ACL sin mutaciones y pruebas activas de DML son pasos distintos.
6. Aprovisionar una cuenta administrativa real sólo mediante el procedimiento autorizado, con datos laborales legítimos y Argon2id, sin credenciales predeterminadas. No configurar neondb_owner en el proceso web.

### C. Pasos que modificarían Render/publicación — también posteriores

1. Implementar previamente el perfil remoto de conexión administrativa: TLS/destino/identidad/capacidades verificados, fallos cerrados y ausencia de fallback. Mantener DATABASE_URL con el lector y añadir AUTH_DATABASE_URL como secreto independiente sólo tras esa revisión. Dimensionar pools por número de instancias/workers.
2. Añadir controles de registro por capacidad para publicar por etapas. Hoy AUTH_ENABLED registra todas las rutas locales, incluidas imágenes, y cloud rechaza true: **no basta con retirar ese rechazo y cambiar la variable**. Primero habilitar autenticación/consultas, luego disponibilidad, después descripción/etiquetas; mantener imágenes cerradas hasta su almacenamiento aprobado.
3. Ensayar navegador React/API sobre los dominios HTTPS reales: login, cookie, CSRF, CORS, expiración, logout, permisos Admin/Staff y errores. Confirmar con observación de conexiones que menú/medios utilizan exclusivamente el lector, incluso con cookie válida o fallo del pool administrativo.
4. Desplegar el código revisado y configurar los secretos/flags gradualmente sólo con autorización explícita. ADMIN_ENABLED permanece false: el mecanismo X-Admin-Key se retirará, no se convierte en acceso de producción.
5. Tras cada etapa verificar menú anónimo, capacidades negativas SQL, auditoría, revocación y límites persistentes. Ante fallo, cerrar las rutas administrativas y retirar/rotar su secreto sin degradar el lector ni conceder ownership como solución temporal.

## 9. Archivos de esta fase y cierre

El único ajuste de código productivo de esta fase es [repositories/auth.py](../../app/repositories/auth.py), para consultar empleado/roles bajo el lock de cuenta existente. Se añaden dos SQL locales, [test_admin_writer.py](../../tests/test_admin_writer.py) y este reporte; las guías anteriores/README apuntan a la fase vigente. No se cambia la configuración cloud, los contratos HTTP ni los esquemas de persistencia.

Fase 4 termina con un diseño y una demostración local de privilegios. La habilitación de React en producción, creación real de roles, migración de Neon y despliegue administrativo quedan para una autorización y fase posteriores.

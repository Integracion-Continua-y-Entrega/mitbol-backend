# Autenticación administrativa: fase 1 de implementación

Fecha: 5 de noviembre de 2026, America/Mexico_City. Rama: `design/admin-auth-security`.

Este documento conserva el cierre de fase 1. Login/sesiones/CSRF están en la [fase 2](admin_auth_phase2.md); la integración local posterior con HU-005/HU-007 y su evidencia están en la [fase 3](admin_auth_phase3.md). No se modificó la estructura de persistencia descrita aquí.

Esta fase implementa persistencia y primitivas de seguridad de la [arquitectura revisada](admin_auth_proposal.md). No implementa login, cookies, CSRF HTTP, autorización FastAPI, altas operativas ni endpoints nuevos. No activa administración en cloud.

## Archivos y alcance

| Archivo | Responsabilidad |
| --- | --- |
| [app/models/auth.py](../../app/models/auth.py) | Los siete modelos de identidad y seguridad, relaciones, índices y restricciones. |
| [app/db/auth_base.py](../../app/db/auth_base.py) | Registro ORM independiente del catálogo; mismas convenciones de nombres y esquema físico propuesto `public`. |
| [app/models/__init__.py](../../app/models/__init__.py) | Exporta los modelos nuevos conservando `Base.metadata` con sus ocho tablas de catálogo. |
| [migrations/env.py](../../migrations/env.py) | Alembic considera ambos metadatos, sin cambiar validación de destinos. |
| [20261105_0004_auth_core.py](../../migrations/versions/20261105_0004_auth_core.py) | Migración incremental revisable sobre `20261102_0003`. |
| [account_identity.py](../../app/core/account_identity.py) | Normalización/validación básica de email. |
| [passwords.py](../../app/core/passwords.py) | Argon2id: hash, verificación y detección de parámetros que necesitan actualización. |
| [permissions.py](../../app/core/permissions.py) | Mapa inmutable de permisos por código de rol y unión para empleados activos. Todavía no autentica solicitudes. |
| [session_tokens.py](../../app/core/session_tokens.py) | Token aleatorio de 256 bits, hash SHA-256 y comparación segura de identificadores canónicos. |
| [session_policy.py](../../app/core/session_policy.py) | Política configurable de expiración, fechas UTC y comprobación pura de validez/revocación/versión. |
| [requirements.txt](../../requirements.txt) | Argon2 y dependencias fijadas. |
| [test_auth_security.py](../../tests/test_auth_security.py) | Pruebas de las primitivas, roles y límites de sesión. |
| [test_auth_persistence.py](../../tests/test_auth_persistence.py) | Restricciones, migración, relaciones, auditoría y concurrencia en PostgreSQL temporal validado. |
| [scripts/test_auth_phase1.py](../../scripts/test_auth_phase1.py) | Ejecutor local de PostgreSQL desechable con pytest y cierre del clúster. |

No se cambiaron `app/main.py`, la protección `ADMIN_ENABLED`, CORS, rutas públicas/administrativas, conexiones cloud ni scripts/grants de Neon. Importar estos modelos no crea tablas, inicia conexiones ni migra al arrancar la API.

## Las siete tablas

| Tabla | Columnas |
| --- | --- |
| `Persons` | `person_id` int PK, `name` varchar(100), `phone` varchar(20), `created_at` timestamptz, `updated_at` timestamptz nullable. |
| `PersonalAccounts` | `account_id` int PK, `person_id` int FK UNIQUE, `email` varchar(254) UNIQUE, `password_hash` text, `is_active` boolean explícito, `auth_version` int DEFAULT 0, `email_verified_at`/`last_login_at` nullable, `created_at`, `updated_at` nullable. Todas las fechas son timestamptz. |
| `Employees` | `person_id` int PK/FK, `employee_code` varchar(30) UNIQUE, `hired_at` date, `is_active` boolean explícito, `created_at` timestamptz. |
| `Roles` | `role_id` int PK, `code` varchar(30) UNIQUE, `name` varchar(50), `description` text nullable. |
| `EmployeeRoles` | PK compuesta por `person_id` FK a Employees y `role_id` FK a Roles. |
| `AccountSessions` | `session_id` UUID PK, `account_id` FK, `token_hash` varchar(64) UNIQUE, `csrf_token_hash` varchar(64) nullable, `auth_version` int, `created_at`, `last_activity_at`, `expires_at`, `idle_expires_at`, `revoked_at` nullable y `revocation_reason` varchar(50) nullable. Fechas timestamptz. |
| `AuditEvents` | `audit_event_id` bigint PK, `actor_account_id` FK nullable, `action` varchar(100), `outcome` varchar(20), `resource_type` varchar(50) nullable, `resource_id` varchar(100) nullable, `request_id` UUID nullable, `failure_code` varchar(50) nullable, `created_at` timestamptz. |

Los identificadores enteros propios usan secuencias PostgreSQL como el catálogo. Employees reutiliza person_id sin secuencia. La PK de sesión es un UUID interno, distinto del secreto de sesión; el futuro creador de sesiones debe proporcionarlo.

Las relaciones Persona–Cuenta y Persona–Empleado son uno a cero/uno; EmployeeRoles conserva muchos a muchos. Sesiones y auditoría se vinculan a la cuenta, no exclusivamente al empleado, por lo que no duplicaremos credenciales cuando existan clientes. No se implementan Customers ni membresías.

Las FKs usan NO ACTION, sin borrado en cascada: no se borran personas/cuentas con referencias para revocar acceso. Las relaciones ORM usan `passive_deletes="all"` y no eliminan hijos silenciosamente. La futura desactivación/revocación debe conservar trazabilidad.

## Ajustes y restricciones respecto del diagrama

- Unicidad de cuenta por persona y email canónico, y de employee_code. No se duplican nombre, teléfono ni rol en PersonalAccounts.
- Roles.code distingue `admin` y `staff` independientemente de los IDs numéricos y nombres visibles. La PK compuesta impide asignaciones repetidas. Otros códigos bien formados no conceden permisos automáticamente.
- auth_version es no negativo y comienza en cero. El incremento para cambios de seguridad debe ser SQL atómico (`auth_version = auth_version + 1`), no asignar desde una lectura obsoleta.
- Email se recorta y convierte a minúsculas al asignarlo por ORM. El CHECK de DB exige su representación normalizada; UNIQUE la hace efectiva frente a escrituras concurrentes. No se eliminan puntos ni sufijos `+`. La validación es básica, no prueba existencia o control del buzón. Correo internacional/colaciones deberá revisarse antes de aceptar registro público.
- password_hash debe comenzar por el formato `$argon2id$`; eso evita almacenar contraseñas ordinarias accidentalmente, pero DB no valida criptográficamente un hash. Sólo la biblioteca verifica su integridad al autenticar.
- Nombres, teléfono y código laboral no pueden ser vacíos. Teléfono e hired_at siguen siendo obligatorios y **no tienen valores ficticios predeterminados**.
- El diagrama recorta el DEFAULT de is_active: cuenta y empleado requieren estado explícito. Este comportamiento evita activaciones implícitas sin inventar un valor del documento original.
- Sesiones exigen digest hexadecimal de 64 caracteres, versión no negativa y `created_at <= last_activity_at < idle_expires_at <= expires_at`. La revocación no puede ser anterior a creación ni tener motivo sin fecha.
- AuditEvents sólo admite resultados success/denied/failure y acciones con formato acotado. No incorpora un JSON arbitrario para cuerpos de solicitud o secretos.

Los nuevos metadatos ORM no son otro sistema de usuarios. Separarlos evita que las herramientas existentes, que enumeran `Base.metadata.tables`, incorporen cuentas/sesiones a conteos, locks o comprobaciones de sincronización. Alembic admite varias colecciones de metadatos y recibe las dos. [Documentación de Alembic](https://alembic.sqlalchemy.org/en/latest/autogenerate.html#autogenerating-multiple-metadata-collections).

## Migración revisable

`20261105_0004`, hija única de `20261102_0003`, crea las siete tablas, sus restricciones e índices. Los índices de sesión permiten búsqueda por cuenta/expiración; el hash único permite resolver una sesión sin almacenar el secreto. La auditoría tiene índice por actor/fecha.

Inserta únicamente dos filas de Roles: admin y staff. No inserta personas, cuentas, empleados, hashes de contraseña ni sesiones. Tampoco crea logins PostgreSQL, concede grants o altera el catálogo. Los DEFAULT temporales de creación son `now()`; no se añaden actualizaciones automáticas escondidas.

`downgrade` elimina primero auditoría/sesiones/asignaciones y después sus tablas padre, sin CASCADE. Es destructivo para esos datos; se probó exclusivamente dentro del clúster temporal. No se ejecutó sobre ninguna base de uso real y no se autoriza usarlo como procedimiento de recuperación.

La migración fue escrita explícitamente y comparada con los modelos en PostgreSQL temporal; no se generó desde Neon ni mitbol_dev. Las revisiones históricas no se modificaron. La revisión de sincronización comercial permanece fijada en `20261102_0003`: esta fase no autoriza sincronizar un futuro esquema migrado a 0004. Esa compatibilidad deberá revisarse cuando se autorice avanzar el esquema real.

## Núcleo de seguridad implementado

### Contraseñas

Argon2id con 64 MiB de memoria, tres pasadas, un hilo, salt de 16 bytes y hash de 32 bytes. El salt lo genera la biblioteca para cada contraseña. Se usan `argon2-cffi==25.1.0` y dependencias fijadas; se verificó compatibilidad instalada con `pip check`.

`hash_password` admite de 15 a 1024 caracteres, sin recortar ni normalizar la contraseña. `verify_password` devuelve false ante contraseña incorrecta o hash inválido, sin aceptar otros algoritmos. `password_needs_rehash` detecta parámetros distintos. Las excepciones propias no incluyen la contraseña. [API de argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/api.html).

No se ha implementado todavía un servicio de login, hash ficticio contra enumeración de cuentas, lista de contraseñas comprometidas ni control de concurrencia del hashing: deben formar parte del flujo protegido de fase 2. Las primitivas no sustituyen esas barreras.

### Roles

Staff obtiene únicamente `catalog:read` y `availability:write`. Admin añade `description:write`, `images:write`, `tags:write` y `settings:write`. Un empleado inactivo no obtiene permisos. La unión admin+staff no amplía los permisos de admin; códigos desconocidos y nombres de roles PostgreSQL no conceden nada.

Esta función no comprueba por sí sola la sesión/cuenta. La futura dependencia FastAPI deberá resolver primero cuenta/sesión y perfil laboral desde DB. `settings:write` no habilita un endpoint genérico ni acceso a secretos: requiere operaciones y claves expresamente autorizadas.

### Identificadores y sesiones

El token de sesión contiene 32 bytes aleatorios codificados como 43 caracteres URL-safe sin padding. Se valida representación canónica antes de calcular SHA-256; identificadores o digests mal formados se rechazan. Ni token ni hash aparecen en el repr del objeto de material secreto. El hash, no el token, se guarda en AccountSessions.

SessionPolicy es una configuración Python independiente del Settings global: ocho horas absolutas y treinta minutos de inactividad por defecto, ajustables con timedelta positivos. No añade variables a Render. Requiere fechas con zona horaria y trabaja en UTC. El próximo límite de inactividad nunca supera el absoluto.

`session_is_valid` comprueba cuenta activa, versión vigente, ausencia de revocación y ambos vencimientos. En el borde exacto de expiración deja de ser válida. No consulta DB ni extiende la sesión: los repositorios y dependencias de fase 2 deben cargar el estado vigente y hacer actualizaciones condicionales.

csrf_token_hash queda nullable porque todavía no se genera CSRF ni hay HTTP autenticado. Si se utiliza esa columna en fase 2, una sesión HTTP administrativa deberá quedar ligada a su protección CSRF antes de autorizar mutaciones. Sólo se almacenará el digest; la emisión/rotación/recuperación del token entre pestañas se definirá entonces sin persistirlo en claro. No se afirma que las sesiones de prueba sean un login listo para publicar.

## Concurrencia y límites de estas pruebas

Se probaron dos conexiones independientes que incrementan auth_version simultáneamente mediante SQL atómico: ambas invalidaciones se conservan. También una sesión leída antes de logout: una actualización ORM posterior de actividad no sobrescribe la revocación ya confirmada, porque sólo escribe los campos modificados.

Esto comprueba propiedades de persistencia, no una autorización concurrente completa. En fase 2, las actualizaciones de actividad deben condicionar sesión no revocada/no expirada, conservar el vencimiento absoluto y volver a consultar la versión/estado. Emisión de sesión, cambio de contraseña/roles y baja necesitan un orden consistente de locks de la cuenta y transacciones que no pierdan invalidaciones. No se debe usar merge de un objeto obsoleto ni guardar revoked_at=None desde un snapshot.

Las operaciones sensibles deben revalidar antes de su mutación. Logout no revierte una operación ya autorizada/en curso. El comportamiento de carreras login/logout/cambio de roles se probará cuando existan sus repositorios y servicios; no se declara implementado aquí.

La prueba de auditoría comprueba que un error al insertar el evento revierte el cambio de la misma transacción. AuditEvents aún no se integra con los commits de HU-005/HU-007 ni es append-only por arte de su modelo: la integración transaccional y los grants restrictivos son trabajos posteriores.

## Procedimiento previsto para primeras cuentas Admin y Staff

No hay cuenta, contraseña ni clave predeterminada. Tampoco se entregó un comando de alta operativo en esta fase. El procedimiento que deberá implementarse/revisarse es:

1. Seleccionar una base local/temporal explícitamente autorizada y verificar destino/operador. Ninguna credencial de owner estará en el backend web.
2. Obtener nombre, teléfono, código de empleado, fecha real de contratación, email y rol autorizado. No deducir que el propietario tiene una fecha de contratación ni inventar datos para cumplir el esquema.
3. Capturar y confirmar una contraseña por entrada oculta; no pasarla por argumentos, URLs, scripts, archivos versionados o logs. Validar política y generar Argon2id antes de persistirla.
4. En una transacción, crear Persons, PersonalAccounts con estado explícito y Employees con sus datos reales; resolver Roles por code y crear EmployeeRoles. No aceptar un role_id arbitrario de una interfaz pública.
5. Registrar un evento de aprovisionamiento sin secretos y confirmar la transacción. Si falla cualquier parte, no dejar una cuenta o empleado a medias. El primer bootstrap tendrá actor de auditoría nullable y quedará identificado por su procedimiento operador.
6. No marcar email_verified_at sin comprobar el buzón mediante el procedimiento que se acuerde. No crear Customers ni membresías como efecto secundario.
7. Documentar la entrega segura de credenciales, recuperación, cambios de roles y revocación. Proteger la última cuenta admin con acceso efectivo.

Para la primera cuenta, la decisión pendiente es de negocio: si el propietario tiene perfil Employees con datos legítimos, se usa el modelo aprobado; si no, hay que revisar su vinculación antes de aprovisionarlo. El resto de la estructura no depende de inventar esos datos.

## Ejecución reproducible y aislamiento

Desde el entorno virtual del proyecto:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra
```

Requiere PostgreSQL 18; usa binarios del PATH o `MITBOL_TEST_PG_BIN`. Crea un clúster con nombre aleatorio bajo TEMP, escucha únicamente en 127.0.0.1 y usa puerto aleatorio. `--with-reader` añade otro clúster temporal para las pruebas de ACL del lector sin compartir sus roles de clúster. Trust sólo existe dentro de esos clústeres locales de pruebas. Los procesos auxiliares Windows se inician ocultos; sus salidas quedan en archivos temporales para evitar bloqueos de handles heredados.

El ejecutor pasa `APP_ENV=test`, una DATABASE_URL ficticia, administración deshabilitada y directorios/puertos explícitos de prueba. Las fixtures comprueban usuario mitbol_ci, versión PostgreSQL 18, ruta data_directory real, nombre del directorio y bases con prefijo aleatorio. No leen .env. El clúster se detiene al terminar y conserva logs/archivos para revisar evidencia; no elimina directorios calculados recursivamente.

Las pruebas de autenticación crean/migran/revierten y eliminan sólo su propia base `mitbol_test_auth_<uuid>` en ese clúster. Las pruebas de catálogo conservan el esquema autorizado `20261102_0003`; CI también lo prepara expresamente en vez de migrar esa fixture a head. Así probamos que la API sigue funcionando sin tablas de cuentas. La fixture nueva de autenticación usa head en otra base. El servicio temporal validado de CI es otra ruta admitida, nunca una URL libre de usuario.

Las llamadas Loyverse de la suite son simuladas. No se ejecutó sincronización real, no se inspeccionaron datos de mitbol_dev y no se accedió a Neon/Render. La prueba de migración incremental usa un catálogo sintético, comprueba conservación de filas y esquema, y compara Alembic con ambas colecciones ORM.

Resultado final: **955 pruebas aprobadas, cero fallos, cero errores, cero omitidas; dos avisos de deprecación existentes de Starlette/AnyIO**, en 238.02 segundos de pytest. Ambos clústeres temporales quedaron detenidos. `pip check` no detectó dependencias incompatibles; sintaxis, enlaces locales y comprobación de whitespace pasaron.

Incluye la nueva prueba real de ACL: con las siete tablas de seguridad creadas en un clúster separado, los grants existentes del lector no permiten SELECT en ninguna; intentar leer password_hash produce denegación. Las regresiones públicas se ejecutan también contra el catálogo anterior sin tablas de identidad. La evidencia de la ejecución queda en `TEMP/mitbol-sync-preflight-0534464350034f40a6fb9f1e4370324b/test-run.log`; sólo contiene datos de pruebas sintéticos.

## Fase 2 y requisitos obligatorios antes de publicación

La siguiente fase, sujeta a revisión, implementará repositorios transaccionales, login/logout/me/logout-all/cambio de contraseña, dependencias de cuenta+empleado+permisos, cookies HttpOnly y CSRF. También limitador de intentos y hashing concurrente con errores genéricos, sin enumeración de cuentas y con limpieza/retención de seguridad definidas.

Al cerrar fase 1 el limitador no estaba implementado. Fase 2 incorpora el limitador local en memoria y mantiene un bloqueo explícito de cloud; antes de exposición o varios workers deberá ser compartido y persistente. No puede omitirse al publicar login. El cambio de almacenamiento no afecta a la identidad única.

React en `http://localhost:5173` sigue consumiendo el menú de `https://mitbol-backend.onrender.com`. La autenticación se probará aparte contra FastAPI y PostgreSQL aislados, inicialmente `http://localhost:8000`. No se despliega login en Render ni se modifica su CORS en esta fase.

La futura cookie local tendrá nombre separado, HttpOnly y SameSite explícitos; producción exigirá Secure/HTTPS. CORS/Origin/CSRF y navegadores reales se comprobarán antes de habilitar las rutas. El dominio público del frontend no se elige ahora.

El escritor PostgreSQL mínimo, los pools separados y la ampliación de privilegios se diseñarán aparte. `mitbol_render_reader` sigue exclusivamente lector del catálogo. El modo legado `ADMIN_ENABLED` no se utilizará para saltar a administración pública ni se sustituirá la conexión web por neondb_owner.

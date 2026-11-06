# MITBOL — Corrección del laboratorio PostgreSQL de GitHub Actions

Fecha: 6 de noviembre de 2026, America/Mexico_City. Rama: `design/admin-auth-security`.

## Evidencia y causa

Se leyó directamente `pytest.xml` dentro de `backend-pytest-results (1).zip`, sin
ejecutar contenido del adjunto. SHA-256 del ZIP:
`5ef0994b426f226b5783580a14acfffaf2bab7a371bca572217c27c8aa6b4b34`.

El informe registra 1252 pruebas: 1122 aprobadas, 130 errores de setup, cero fallos
y cero omisiones. Distribución: test_admin_writer 103, test_auth_phase5 24 y
test_account_provisioning 3. La excepción común es `WRITER_LOCAL_TARGET` al
preparar el escritor, antes de crear el rol o conceder sus permisos.

| Condición | Evidencia de la ejecución fallida |
| --- | --- |
| PostgreSQL 18 | La fixture auth_database comprueba `SHOW server_version_num` antes de crear la base y migrarla. Se llegó después al SQL del escritor; el workflow especificaba postgres:18. No se conoce la versión menor de ese contenedor. |
| Base temporal con UUID hexadecimal de 32 caracteres | JUnit registra nombres válidos. El traceback muestra `mitbol_test_auth_c73b1805abfb461793802c31baf73b89`. |
| current_user y session_user iguales a mitbol_ci | Antes del escritor se completó prepare_render_reader.sql, cuya copia temporal exige ambos valores y la base exacta. No cambia la identidad de sesión. |
| TCP, sin socket Unix | El engine del traceback conecta a 127.0.0.1:5432 con psycopg2 y sin parámetros alternativos de host. No es una conexión por socket Unix. |
| inet_server_addr() en loopback | Es la condición restante del primer guard. CI publicaba 5432 del contenedor Docker; su dirección interna no es la dirección loopback del cliente en el runner. |

La condición que provoca el rechazo se identifica por el reporte y el orden real
de los guards: **la dirección del servidor observada por PostgreSQL no satisface
el loopback exigido**. No se confunde la IP de destino de la URL del cliente con
la IP donde PostgreSQL acepta la conexión. Docker publica puertos mediante NAT;
inet_server_addr() devuelve la dirección del servidor, no la del cliente.
Véanse [Docker: publicación de puertos](https://docs.docker.com/engine/network/port-publishing/),
[PostgreSQL 18: funciones de información](https://www.postgresql.org/docs/18/functions-info.html)
y [GitHub: servicios PostgreSQL](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

**Límite de la evidencia:** el XML anterior no registra el valor numérico de
inet_server_addr(). No se inventa una dirección 172.x concreta ni se afirma haber
reproducido ese contenedor. Docker Desktop no tiene un daemon disponible en este
equipo. La siguiente ejecución de Actions sigue siendo necesaria.

En local, el ejecutor existente inicia postgres con `-h 127.0.0.1`, conecta por
TCP directamente y contrasta data_directory con su ruta temporal generada. Allí
cliente y servidor comparten loopback, sin traducción Docker. El guard SQL es
correcto para ese laboratorio; no se amplía a redes privadas ni a sockets Unix.

## Corrección

1. [backend-ci.yml](../../.github/workflows/backend-ci.yml) sustituye el servicio
   Docker por binarios PostgreSQL 18 del repositorio oficial PGDG. La imagen
   Ubuntu 24.04 incluye PostgreSQL 16; no se usa ese valor por defecto. El
   repositorio añadido utiliza HTTPS y Signed-By; apt verifica la firma. Se
   selecciona explícitamente `/usr/lib/postgresql/18/bin`.
2. CI llama al [ejecutor existente](../../scripts/test_auth_phase1.py) con
   `--with-reader`: dos clústeres nuevos, initdb -U mitbol_ci, puertos aleatorios,
   escucha TCP sólo en 127.0.0.1 y cierre de ambos en finally. No se necesita
   DATABASE_URL de un servicio externo ni credenciales reales.
3. [La fixture](../../tests/test_auth_persistence.py) registra antes de validar
   `auth_control_target` y `auth_isolated_target` en JUnit: versión numérica, base,
   current_user, session_user, dirección y puerto reales del servidor. Rechaza
   versión/identidades/dirección incompatibles antes de crear bases; vuelve a
   validar en la conexión de la base temporal antes de migrar. No registra DSN,
   contraseñas, hashes ni tokens.
4. Se añaden seis regresiones en [test_ci_configuration.py](../../tests/test_ci_configuration.py)
   que prueban el rechazo antes de CREATE DATABASE ante versión incorrecta,
   cualquiera de las identidades incorrecta, socket Unix (dirección NULL), IP de
   bridge Docker simulada e IP no autorizada. Dos regresiones adicionales en
   [test_admin_writer.py](../../tests/test_admin_writer.py) ejecutan el script real
   con los logins limitados del lector y escritor, comprueban el rechazo y
   verifican que las ACL no cambiaron.
5. [README](../../README.MD#integración-continua-con-github-actions) documenta el
   nuevo comando y cómo actualizar el PR existente, sin ejecutar Git remoto.

El ejecutor limpia el entorno heredado para seleccionar sus fixtures de
laboratorio, incluyendo GITHUB_ACTIONS únicamente en el proceso hijo pytest.
Activa ambas suites PostgreSQL y mantiene APP_ENV=test, ADMIN_ENABLED=false y
AUTH_ENABLED=false. Esto no omite tests: no se pasan filtros, exclusiones,
xfail ni continue-on-error. Las validaciones de la ruta alternativa de servicio
CI siguen cubiertas por sus pruebas. La selección por directorio temporal añade
la comprobación real de data_directory.

La instalación de paquetes en el runner puede crear un clúster predeterminado
del sistema; las pruebas no lo usan. Sus clústeres tienen rutas nuevas verificadas
y puertos propios. La [lista oficial de Ubuntu 24.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
y las [instrucciones oficiales PGDG](https://www.postgresql.org/download/linux/ubuntu/)
fundamentan la selección explícita de PostgreSQL 18. Se fija la versión mayor y
las dependencias Python; el parche PostgreSQL y la imagen hospedada pueden
actualizarse. La versión efectiva queda registrada en el XML.

## Protecciones conservadas

- prepare_admin_writer_local.sql y verify_admin_writer_local.sql están intactos:
  PG18, base con UUID, ambas identidades esperadas, rechazo de NULL y TCP fuera de
  127.0.0.1/::1. También permanecen los guards de revisión del esquema y rol nuevo.
- Scripts Neon, ACL locales/remotas, migraciones, modelos y código de producción
  sin cambios. No se añaden grants al escritor ni escritura al lector.
- La suite conserva las operaciones SQL prohibidas sobre precios, identificadores
  Loyverse, identidad, roles, auditoría y secuencias; el lector no puede escribir
  ni leer autenticación, sesiones, auditoría o límites.
- Continúan sesiones revocables, CSRF, CORS por ámbito, límites persistentes,
  restricciones por rol, registro gradual y rollback conjunto de operación y
  auditoría. El menú público sigue separado de la conexión administrativa.
- Cloud administrativo sigue bloqueado; no se cambia la decisión de dominio
  pendiente ni se habilitan imágenes remotas.
- Se mantienen acciones fijadas a SHA, contents:read, checkout sin credenciales,
  ejecución por pull_request (sin pull_request_target), propagación de errores y
  artefacto JUnit siempre. No se introducen secretos de repositorio.

## Validación

Pruebas seleccionadas, con cuentas/roles exclusivamente sintéticos:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra --tb=short "--junitxml=$env:TEMP\mitbol-admin-ci-targeted-20261106.xml" tests/test_admin_writer.py tests/test_auth_phase5.py tests/test_account_provisioning.py tests/test_ci_configuration.py
```

**163 aprobadas, cero errores/fallos/omisiones**, en 65.93 segundos: escritor 105,
fase 5 29, aprovisionamiento 13 y configuración CI 16. Ambos clústeres detenidos.
JUnit confirma PostgreSQL 18.2 (`180002`), current_user=session_user=mitbol_ci,
bases mitbol_test_auth con UUID y dirección real del servidor 127.0.0.1.
Log: `%TEMP%\mitbol-sync-preflight-75f0fcb5e6034b5a8c93a026270a23ed\test-run.log`.

Suite completa con el mismo ejecutor y selección de pruebas del workflow:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_auth_phase1.py --with-reader -q -ra --tb=short "--junitxml=$env:TEMP\mitbol-admin-ci-full-20261106.xml"
```

**1260 aprobadas, cero errores/fallos/omisiones**, en 442.50 segundos. Se conservan
las 1252 pruebas del informe original y se añaden ocho regresiones. Las pruebas
comprueban el lector sin escritura, rechazo SQL de cambios de precios e IDs
Loyverse, auditoría en la misma transacción y rollback ante sus fallos. Ambos
clústeres quedaron detenidos y el ejecutor devolvió código cero.
Log: `%TEMP%\mitbol-sync-preflight-a5f65be827e34bf281f2f9ea75df5b95\test-run.log`.

Comprobaciones adicionales: actionlint 1.7.12 aprobó el workflow; se descargó
fuera del repositorio y se verificó el SHA-256 contra los checksums oficiales.
Los dos bloques Bash del workflow pasaron `bash -n` en Ubuntu/WSL, sin ejecutar
sus instrucciones. `pip check`, `git diff --check` y
`render_admin_deployment_sql.py --check` aprobados. No se instalaron
dependencias nuevas en el proyecto. Las dos advertencias de deprecación existentes
de Starlette/AnyIO se conservan; no se silencian.

**Estado de CI:** validación local en Windows con Python 3.13.13 y PostgreSQL 18.2.
No se ejecutó el runner hospedado Ubuntu ni la instalación apt de ese job. No se
afirma que GitHub Actions esté verde. El cambio queda preparado para commit y
push del usuario a design/admin-auth-security, que actualizarán el PR y permitirán
confirmarlo allí. No se ejecutaron commits, push, despliegues, sincronizaciones,
creación de cuentas reales ni migraciones sobre bases reales. develop permanece
intacto.

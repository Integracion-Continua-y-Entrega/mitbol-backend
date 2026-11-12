# HU-006 — Publicación del backend y actualización del escritor

Estado: procedimiento preparado; no ejecutado en Render/Neon. No acredita
despliegue ni aceptación del frontend. Se aplica al servicio académico existente,
sin crear otra base, rama Neon o servicio Render.

## Qué cambia en cada sistema

- **GitHub:** código y perfiles SQL de HU-006, mediante PR hacia `develop` y CI.
- **Render:** publica esa versión del backend y conserva su configuración vigente.
- **Neon:** concede al escritor existente UPDATE de `is_featured` e `is_new`.
  No cambia filas de productos ni crea tablas o cuentas.

La versión anterior y la nueva exigen perfiles de permisos exactos diferentes.
Durante la transición se deshabilita administración; el menú público permanece
disponible con el lector existente. No actualizar solo código o solo permisos
mientras siga funcionando administración con el otro perfil.

## 1. Preparación y registro del estado actual

Antes de ejecutar cambios:

1. En Render, confirmar servicio, rama publicada y opción Auto-Deploy.
   Guardar el SHA actualmente publicado y el valor de `AUTH_ENABLED`.
2. Confirmar en Neon proyecto, rama y base `mitbol_cloud_dev`; obtener el hostname
   **directo** autorizado desde su consola y comprobar que el operador es
   `neondb_owner`. No utilizar una base de desarrollo local ni otra rama.
3. Preparar PR de HU-006 y exigir `Backend tests` aprobado para el commit elegido.
   No fusionar aún si esa acción puede desplegar automáticamente.
4. Conservar los valores actuales de las variables y el estado de los dos
   permisos; no copiar contraseñas o cadenas de conexión a Git, PR o capturas.

## 2. Cerrar temporalmente administración en Render

1. Poner Auto-Deploy en **Off** durante esta transición.
2. Establecer `AUTH_ENABLED=false` y aplicar el cambio mediante despliegue de la
   versión actual. Guardar variables con **Save only** no las activa por sí solo.
3. Esperar a que Render marque el despliegue como activo. Confirmar que
   `/api/menu` responde y que las rutas de autenticación/administración están
   deshabilitadas. `ADMIN_ENABLED` permanece `false`.

No modificar URLs de base, claves, cookies, perfil de autenticación ni CORS como
parte de HU-006. No iniciar una migración o sincronización Loyverse.

## 3. Actualización incremental en Neon

El archivo [update_admin_writer_highlights.sql](../database/update_admin_writer_highlights.sql)
actualiza un rol existente. Los scripts `prepare_admin_writer_*` crean roles
nuevos y no deben reejecutarse para esta actualización.

Ejemplo de conexión independiente con psql 18 desde la raíz del proyecto en
PowerShell. Sustituir el hostname por el directo autorizado; no pegar una URL
con contraseña. psql pedirá la contraseña sin guardarla en el comando:

```powershell
$env:PGHOST = 'HOST_DIRECTO_CONFIRMADO_EN_NEON'
$env:PGPORT = '5432'
$env:PGDATABASE = 'mitbol_cloud_dev'
$env:PGUSER = 'neondb_owner'
$env:PGSSLMODE = 'verify-full'
$env:PGCHANNELBINDING = 'require'
$env:PGSSLMINPROTOCOLVERSION = 'TLSv1.2'
$env:PGSSLROOTCERT = & .\venv\Scripts\python.exe -c 'import certifi; print(certifi.where())'
& 'D:\PostgreSQL\18\bin\psql.exe' -X --password --set=ON_ERROR_STOP=1
```

Usar una terminal dedicada sin overrides previos de `PGHOSTADDR`, `PGSERVICE`,
`PGSERVICEFILE`, `PGOPTIONS` o `PGPASSWORD`. Si están configurados, revisar y
retirarlos de esa terminal antes de conectar. La contraseña se introduce en el
prompt, no mediante una variable o argumento.

Dentro de psql, comprobar y conservar estos resultados de preflight:

```sql
\conninfo
SELECT current_database(), current_user, session_user;
SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();
SELECT version_num FROM public.alembic_version;
SELECT column_name,
       has_column_privilege('mitbol_admin_writer', 'public."Products"', column_name, 'UPDATE') AS can_update
FROM (VALUES ('description'), ('is_available'), ('is_featured'), ('is_new')) AS fields(column_name);
```

Destino esperado: `mitbol_cloud_dev`, usuario y sesión `neondb_owner`, PG18,
TLS activo y revisión `20261106_0005`. Si no coinciden, detenerse y revisar.
El perfil anterior tiene UPDATE en los dos primeros campos y no en los dos
indicadores. Si el estado difiere, reconciliarlo antes de seguir.

Tras comprobar la conexión con `verify-full` y channel binding obligatorio,
en la **misma sesión**:

```sql
SET mitbol.approved_admin_write = 'hu006-reviewed';
SET mitbol.hu006_direction = 'upgrade';
SET mitbol.external_tls_verified = 'verify-full-channel-binding';
\i docs/database/update_admin_writer_highlights.sql
```

El script verifica destino, operador, versión, revisión y características del
rol; modifica solo dos grants y confirma en una transacción. Las marcas de
sesión no prueban TLS por sí mismas: se establecen después de conectarse con
los parámetros anteriores. Ante cualquier error, no continuar con la reapertura.

Salir con `\q`. Conectar de nuevo cambiando únicamente `PGUSER` a
`mitbol_admin_writer` e introduciendo su contraseña correspondiente. Es necesario
un LOGIN real independiente, no `SET ROLE`. En esa conexión:

```sql
SET mitbol.external_tls_verified = 'verify-full-channel-binding';
\i docs/database/verify_admin_writer_neon.sql
```

Debe devolver `WRITER_POLICY_OK_REVIEWED_PROFILE`. Verificar también que el lector
conserva su perfil de solo lectura mediante el procedimiento vigente del proyecto.

## 4. Publicar HU-006 en Render

1. Fusionar el PR aprobado hacia la rama de integración acordada. Si el servicio
   está vinculado a otra rama, resolver esa diferencia antes de desplegar.
2. Con `AUTH_ENABLED=false` y Auto-Deploy aún desactivado, realizar despliegue
   manual del commit que contiene HU-006. Confirmar el SHA en Render.
3. Comprobar `/`, `/api/menu`, `/api/menu/config`, `/api/menu/tags` y un detalle
   válido. Menú/detalle deben incluir `is_featured` e `is_new`; no aparece un
   producto comercialmente inactivo por tener marcas.
4. Restaurar el valor autorizado anterior de `AUTH_ENABLED` y aplicarlo con un
   despliegue. Si antes era false, no habilitar administración sin preparar y
   autorizar su configuración y cuentas como una operación aparte.
5. Con sesión Admin y Origin/CSRF válidos: consultar indicadores, editar un
   producto de prueba acordado y comprobar menú/detalle. Restaurar sus marcas
   originales después de la prueba. Staff debe leer y recibir 403 al editar.
6. Comprobar desde frontend indicadores visuales y que las marcas no habiliten
   pedidos de productos agotados. Registrar la aceptación del equipo.
7. Restaurar la política de Auto-Deploy acordada solo tras el resultado correcto.

## Reversión

1. Mantener o volver a poner `AUTH_ENABLED=false` y comprobar que está aplicado.
2. Como operador del mismo destino, usar el script incremental con
   `mitbol.hu006_direction='rollback'` para retirar solo los dos grants añadidos
   por esta publicación. No retirar permisos preexistentes sin revisar el registro.
3. Restaurar la versión anterior del backend/verificador en Render, con
   administración deshabilitada. Verificar el perfil anterior con la versión
   anterior de su archivo SQL: el verificador nuevo exige los grants de HU-006.
4. Restablecer la configuración autorizada y repetir comprobaciones. No borrar
   roles, tablas ni productos. Render puede desactivar Auto-Deploy al hacer rollback;
   revisar esa opción antes de reanudar publicaciones automáticas.

## Evidencia de cierre

Registrar commit/PR, CI, SHA publicado, comprobación SQL del escritor y del
lector, pruebas HTTP y aceptación del frontend. Hasta tener esa evidencia la
entrega está preparada, pero no desplegada ni aceptada de extremo a extremo.

Referencias oficiales: [variables en Render](https://render.com/docs/configure-environment-variables),
[despliegues](https://render.com/docs/deploys),
[rollback](https://render.com/docs/rollbacks),
[variables libpq](https://www.postgresql.org/docs/18/libpq-envars.html).

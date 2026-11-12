# HU-009 — Migración y publicación

Estado de este documento: procedimiento preparado; no ejecutado en una base
local de trabajo, Neon o Render. El pipeline del PR ejecuta pruebas y no hace
este despliegue. La guía de aprendizaje no forma parte de la publicación.

## Base y permisos

Para una instalación existente, comprobar el destino con una conexión de
operador y confirmar revisión `20261106_0005`. Verificar respaldo y que no haya
una tabla Discounts preexistente ajena a Alembic. Si la instalación está en una
revisión anterior, revisar primero el procedimiento de autenticación; no marcar
revisiones como aplicadas ni reemplazar tablas existentes.

Las variables y URLs de operador se configuran fuera de Git y del chat. En
cloud se reutilizan las comprobaciones de destino y TLS del repositorio:
`APP_ENV=cloud`, base/host autorizados y endpoint directo. Una marca de sesión
en SQL no acredita TLS por sí sola.

1. Cerrar temporalmente la administración: desactivar `AUTH_ENABLED`,
   `AUTH_CATALOG_ENABLED` y `AUTH_IMAGES_ENABLED`; mantener `ADMIN_ENABLED=false`
   en cloud. No cambiar el pool público de lectura. Coordinar también la
   publicación previa de HU-006 y su perfil de destacados.
2. Con el operador y destino verificados, revisar el SQL incremental con
   `python -m alembic upgrade 20261106_0005:20261111_0006 --sql`.
3. Aplicar `python -m alembic upgrade 20261111_0006`. Crea únicamente Discounts;
   no sincroniza datos ni crea el rol web. El backend no migra al iniciar.
4. Si existe `mitbol_admin_writer`, ejecutar en la misma sesión del operador
   `SET mitbol.approved_admin_write = 'hu009-reviewed';` y después
   [update_admin_writer_discounts.sql](../database/update_admin_writer_discounts.sql).
   En Neon se exige además TLS externo verificado y la marca correspondiente
   que ya usa el procedimiento de autenticación. El script solo concede SELECT
   por columna y rechaza roles con permisos comerciales peligrosos.
5. Para un rol nuevo, los scripts `prepare_admin_writer_local.sql` (solo
   laboratorio) y `prepare_admin_writer_neon.sql` reconocen 0005/0006 y conceden
   lectura de Discounts solo en 0006. No recrear un rol que ya existe.
6. Publicar este backend con la administración todavía cerrada. Los perfiles
   SQL de escritor anteriores rechazan el grant nuevo; por eso código y ACL
   deben coordinarse en la misma ventana y con pools nuevos.
7. Verificar desde un LOGIN independiente con
   `verify_admin_writer_neon.sql` (o el verificador local en laboratorio).
   Verificar también el lector con `verify_render_reader.sql`: continúa sin
   acceso a Discounts ni a su secuencia. No usar SET ROLE como sustituto de LOGIN.
8. Restaurar la configuración administrativa aprobada y comprobar login,
   `GET /api/admin/discounts`, error 401 sin sesión y 422 por paginación inválida.
   Una instalación anterior sin Discounts devuelve 503 en esta consulta; el
   resto del catálogo conserva su perfil.

En una base local nueva y vacía, crear primero la base con PostgreSQL y después
aplicar Alembic. La configuración local de Alembic admite `.env`; el comando
de sincronización requiere variables de proceso explícitas.

## Carga de descuentos

Desde una terminal operativa con la base migrada, ejecutar el preflight descrito
en [HU-009](../features/hu009-discount-sync.md). Revisar destino, revisión y
permisos antes de usar `--execute`. El comando exige token solo para ejecutar.
La primera importación real y su contraste con Loyverse quedan pendientes de
la autorización y configuración del operador; no forman parte del laboratorio.

Comprobar con Admin y Staff el listado, valores nulos y paginación. React debe
tratar el decimal como cadena, no interpretar puntos como dinero ni calcular
beneficios a partir de `scope=unknown`. Esta revisión de frontend no se ha
realizado desde este repositorio.

## Reversión

Ante un fallo de publicación, cerrar la administración y revisar código, ACL y
revisión antes de reactivar. Volver al backend anterior exige coordinar también
el perfil SQL anterior: no admite el nuevo SELECT de Discounts.

`alembic downgrade 20261106_0005` elimina Discounts y sus datos, conservando las
tablas anteriores. Solo debe usarse con respaldo, ventana aprobada y el rol web
fuera de servicio; no se ejecuta como reparación automática. Un fallo de descarga
o persistencia no necesita downgrade: la transacción conserva la versión válida.

## Git y aceptación

La rama HU-009 nació de `feature/hu-006-product-highlights`. Comprobar el estado
real de HU-006 antes de abrir el PR: si no está integrado, usarlo como base; si
ya está integrado, actualizar HU-009 y revisar el diff contra `develop`.
Exigir el check Backend tests. Commit, push, PR, merge y despliegue son pasos
distintos; una prueba local aprobada no demuestra CI remoto o despliegue exitoso.

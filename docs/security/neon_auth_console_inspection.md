# MITBOL — Preflight de Neon completado

**NO-GO para reintentar 0004 hasta diagnosticar su fallo y recibir autorización.**
El operador confirmó después del intento: sigue 0003 y ninguna de las siete
tablas de autenticación existe. El ejecutor antiguo ocultó la causa. Usar el
[diagnóstico 6.7 de sólo lectura](neon_auth_migration_preflight.md#67-diagnóstico-del-intento-fallido-sólo-lectura).
No repetir A–E ni restaurar. SQL y snapshot están confirmados. Punto existente: `mitbol-before-auth-migrations`,
production, **2026-11-06 19:20 UTC** (13:20 CST, UTC−6), 39.95 MB, Never, con
Restore disponible. Su ID interno no aparece en Console y no es necesario para
este procedimiento sin API: nombre/rama/instante bastan para identificarlo.
No hay otra auditoría, dato de snapshot obligatorio ni nueva creación pendiente.

Trabajo en `audit/neon-auth-migration-preflight`, sin commits ni push. Esta
actualización es documentación local; el agente no se conectó a Neon ni ejecutó
migraciones, backups, restauraciones o cambios de permisos/Render.

**Evidencia confirmada por el usuario.**

| Comprobación | Resultado comunicado |
| --- | --- |
| Destino e identidad | Console, production, mitbol_cloud_dev, neondb_owner, PostgreSQL 18.6, public y transacción de sólo lectura. |
| Alembic | 20261102_0003. |
| Estructura comercial | Ocho tablas y columnas recuperadas; 14 índices válidos/listos y restricciones mostradas validadas. |
| A | Conteos y SHA-256 de las ocho tablas registrados por el operador. |
| B | conflicts=0. |
| C | Permisos del operador y restricciones del lector correctos. |
| D | any_write=false y any_sequence_privilege=false en todas las filas. |
| E | alerts=0. |
| Recuperación | Free y seis horas de historial; snapshot mitbol-before-auth-migrations creado/verificado en production, 2026-11-06 19:20 UTC, 39.95 MB, Never, con Restore disponible. Sin restauración ensayada. |

No se adjuntaron aquí los valores completos de A ni todas las celdas originales:
se registra la confirmación del operador, sin inventar resultados. Conservar
su baseline para las comparaciones. La disponibilidad de botones no demuestra
que una operación haya terminado, que todas las cuotas/permisos permitan usarla
ni que se haya ensayado una restauración.

El error anterior `Cannot read properties of undefined (reading 'map')` apuntaba
al cliente de Console, sin SQLSTATE comunicado. No queda como bloqueo SQL tras
haberse recuperado todas las comprobaciones pendientes A–E.

**Procedimiento mínimo propuesto, sólo tras autorización.**

1. Coordinar una intervención atendida sin escrituras/sincronizaciones del
   catálogo. Conservar A y el SHA aprobado. Si los datos cambiaron desde A,
   refrescar sólo ese baseline antes de capturar el punto; no repetir 0/1 ni
   estructuras ya verificadas sin un cambio que lo justifique.
2. Conservar el snapshot existente y su registro indicado arriba junto a A.
   No crear, borrar, restaurar ni pedir claves/API para obtener su ID interno.
   Confirmar el endpoint directo de production antes de la conexión de ejecución.
3. Ejecutar únicamente la preparación local del apartado 6.2 del
   [informe](neon_auth_migration_preflight.md): carga la función controlada y
   compila el bloque parametrizado, sin conectarse ni pedir contraseña. Detenerse
   aquí para autorización explícita de DDL. Se permite la llamada con `-Diagnose`
   descrita en 6.7, que no ejecuta ninguna revisión.
4. Después de autorizar: `& $MitbolRun -Revision '20261105_0004'`. Su contraseña
   se introduce oculta; no imprimir URL/entorno ni usar transcripción. Valida
   destino, TLS efectivo del driver, roles, PG18 y revisión anterior. Verificar
   siete tablas, Roles con admin/staff y las demás vacías. Comparar A/D y GET.
5. Sólo si todo coincide: `& $MitbolRun -Revision '20261106_0005'`; verificar sus dos tablas
   vacías, índice válido y revisión final. Comparar de nuevo A/D y GET públicos.
   No ejecutar upgrade head, stamp ni autogenerate.
6. Mantener AUTH_ENABLED=false, ADMIN_ENABLED=false y Render con el lector.
   Registrar resultados y conservar el snapshot. Sin escritor PostgreSQL,
   cuentas, cambios de frontend ni sincronización Loyverse.

Los comandos y SELECT posteriores exactos están en 6.2–6.6 del informe; no se
ejecutaron las escrituras. Cada llamada aplica sólo una revisión y limpia el
entorno al terminar, sin avance automático a la siguiente. Los prompts no
sustituyen la autorización del usuario.

**Recuperación y parada.** Si una revisión falla, no continuar: consultar el
estado con conexión nueva. Un rollback de 0005 no deshace 0004 ya confirmada;
mantenerse en 0004 puede ser la opción correcta si el catálogo sigue sano. No
aplicar downgrade, stamp ni restauración automáticamente. Ante daños de datos,
revisar y autorizar PITR o recuperación desde el snapshot, considerando todas
las bases de la rama y los cambios posteriores que se perderían.

Free incluye un snapshot manual según [planes de Neon](https://neon.com/docs/introduction/plans).
Mientras se conserve, el snapshot puede preservar el punto fuera del historial
PITR; no es una copia externa independiente ni una promesa de retención ilimitada
([snapshots](https://neon.com/blog/three-ways-to-use-your-snapshots)). Restaurarlo
puede crear una rama y requerir un paso de selección/finalización del destino
([flujo oficial](https://neon.com/blog/neon-sdk)). No se asume que un botón
Restore devuelva production sin otras decisiones.

PITR sigue sujeto al rango realmente retenido, máximo seis horas/1 GB en Free,
condiciones de la rama y permisos/cupo. No es necesario añadir pg_dump, Time
Travel, otra rama o un ensayo completo a este cambio aditivo; se reconoce que
no se ha probado una restauración. Si el snapshot no puede crearse/usarse,
acordar otra vía antes de dar GO de ejecución.

**Controles conservados.** TLS de Console no se deduce de pg_stat_ssl; los
clientes externos mantienen verify-full, CA, hostname, channel_binding=require
y validación efectiva del driver. Los scripts externos, la aplicación y las
migraciones no cambiaron. El archivo
[SELECT A–E](../database/neon_auth_migration_console.sql) se conserva como
evidencia/herramienta: A/D se reutilizarán después de cada revisión; B no se
repite después porque los objetos nuevos ya deben existir.

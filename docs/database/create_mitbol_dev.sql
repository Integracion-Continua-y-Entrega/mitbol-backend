-- Ejecutado el 2026-10-29 con autorización para crear mitbol_dev.
-- Ejecutar conectado a la base de mantenimiento postgres, fuera de BEGIN.
-- No copia las tablas ni los datos del prototipo mitbol_db.
CREATE DATABASE mitbol_dev
    WITH OWNER = postgres
         TEMPLATE = template0
         ENCODING = 'UTF8';

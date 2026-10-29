from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="API Menú Digital - mitbol",
    description="Backend para la gestión del menú y sincronización con Loyverse",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Alembic administra el esquema; iniciar FastAPI no crea ni modifica tablas.
# El router del prototipo se habilitará cuando utilice el catálogo de HU-008.

@app.get("/")
def read_root():
    return {"mensaje": "¡La API de mitbol está funcionando correctamente!"}

@app.get("/api/menu")
def get_menu_test():
    return [
        {"id": 1, "nombre": "Mitbol Clásica", "precio": 120.00, "disponible": True},
        {"id": 2, "nombre": "Mitbol Vegana", "precio": 135.00, "disponible": True}
    ]

from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "1.0.0"

app = FastAPI(
    title="API Menú Digital - mitbol",
    description="Backend para la gestión del menú y sincronización con Loyverse",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"mensaje": "¡La API de mitbol está funcionando correctamente!"}

@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "ok",
        "service": "mitbol-menu-api",
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/api/menu")
def get_menu_test():
    return [
        {"id": 1, "nombre": "Mitbol Clásica", "precio": 120.00, "disponible": True},
        {"id": 2, "nombre": "Mitbol Vegana", "precio": 135.00, "disponible": True}
    ]
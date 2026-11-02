from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.menu import router as menu_router
from app.schemas.menu import MenuResponse

app = FastAPI(
    title="API Menú Digital - mitbol",
    description="Backend para la gestión del menú y sincronización con Loyverse",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Alembic administra el esquema; iniciar FastAPI no crea ni modifica tablas.
# El router de sincronización del prototipo permanece deshabilitado.

app.include_router(menu_router)


@app.get("/")
def read_root():
    return {"mensaje": "¡La API de mitbol está funcionando correctamente!"}


_default_openapi = app.openapi


def openapi_with_menu_examples():
    schema = _default_openapi()
    # FastAPI elimina null al generar OpenAPI; los ejemplos deben conservar el contrato.
    schema["components"]["schemas"]["MenuResponse"]["examples"] = (
        MenuResponse.model_json_schema(mode="serialization")["examples"]
    )
    return schema


app.openapi = openapi_with_menu_examples

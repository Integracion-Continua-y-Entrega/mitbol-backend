"""Validación pública de modalidad y contacto, sin sesión ni persistencia."""

from fastapi import APIRouter, Response

from app.schemas.order_modality import OrderModalityInput, OrderModalityValidationResponse

router = APIRouter(prefix="/api/order-modality", tags=["Modalidad"])


@router.post(
    "/validate", response_model=OrderModalityValidationResponse,
    summary="Validar modalidad y datos del solicitante",
    description=(
        "Disponible para invitados y usuarios registrados, sin consultar PostgreSQL. "
        "dine_in requiere únicamente name (nombre o alias); pickup requiere name y phone. "
        "Se recortan espacios exteriores del nombre y se normaliza el formato del teléfono. "
        "No admite domicilio, mesa ni datos de reparto. No crea pedidos ni modifica perfiles."
    ),
    responses={422: {"description": "Modalidad no admitida, datos obligatorios ausentes o campos inválidos."}},
)
def validate_order_modality(payload: OrderModalityInput, response: Response) -> OrderModalityValidationResponse:
    response.headers["Cache-Control"] = "no-store"
    return OrderModalityValidationResponse(data=payload)

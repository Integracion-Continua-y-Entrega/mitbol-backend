"""Administración de metas de fidelización por sesión de Admin, con Origin y CSRF."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth_dependencies import require_permission
from app.schemas.loyalty import LoyaltyGoalInput, LoyaltyGoalList, LoyaltyGoalRead, LoyaltyGoalUpdate, LoyaltyGoalVersion
from app.services import loyalty_configuration_service as service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/admin/loyalty/goals", tags=["Configuración de fidelización"])
Reader = Annotated[Principal, Depends(require_permission("loyalty:read"))]
Writer = Annotated[Principal, Depends(require_permission("loyalty:write"))]


@router.get("", response_model=LoyaltyGoalList, summary="Consultar metas de fidelización")
def list_goals(request: Request, response: Response, user: Reader,
               is_active: Annotated[bool | None, Query()] = None,
               limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0):
    response.headers["Cache-Control"] = "no-store"
    return service.list_goals(request.state.auth_context.db, is_active=is_active, limit=limit, offset=offset)


@router.get("/{goal_id}", response_model=LoyaltyGoalRead, summary="Consultar configuración de una meta")
def get_goal(goal_id: UUID, request: Request, response: Response, user: Reader):
    response.headers["Cache-Control"] = "no-store"
    return service.get_goal(request.state.auth_context.db, goal_id)


@router.post("", response_model=LoyaltyGoalRead, status_code=201, summary="Crear una meta de fidelización")
def create_goal(payload: LoyaltyGoalInput, request: Request, response: Response, user: Writer):
    """Valida el catálogo local sin emitir cupones; una meta nueva queda inactiva si no se activa expresamente."""
    response.headers["Cache-Control"] = "no-store"
    return service.create_goal(request.state.auth_context.db, payload, actor_account_id=user.account_id)


@router.put("/{goal_id}", response_model=LoyaltyGoalRead, summary="Actualizar una meta con control de versión")
def update_goal(goal_id: UUID, payload: LoyaltyGoalUpdate, request: Request, response: Response, user: Writer):
    """Recibe la configuración completa y su versión actual; conserva la identidad de la meta."""
    response.headers["Cache-Control"] = "no-store"
    return service.update_goal(request.state.auth_context.db, goal_id, payload, actor_account_id=user.account_id)


@router.post("/{goal_id}/deactivate", response_model=LoyaltyGoalRead, summary="Desactivar una meta")
def deactivate_goal(goal_id: UUID, payload: LoyaltyGoalVersion, request: Request, response: Response, user: Writer):
    """Retira la meta con su versión actual, incluso si el descuento dejó de estar disponible."""
    response.headers["Cache-Control"] = "no-store"
    return service.deactivate_goal(request.state.auth_context.db, goal_id, payload, actor_account_id=user.account_id)

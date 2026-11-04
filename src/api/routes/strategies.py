"""Strategy templates, validation, configuration, and versioning endpoints."""

from typing import List
from fastapi import APIRouter, HTTPException
from src.api.models import (
    StrategyConfigCreateRequest,
    StrategyConfigVersionRequest,
    StrategyConfigVersionResponse,
    StrategyTemplateResponse,
    StrategyValidateRequest,
    StrategyValidateResponse,
)
from src.api.service import workstation_service

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("/templates", response_model=List[StrategyTemplateResponse])
async def list_strategy_templates() -> List[StrategyTemplateResponse]:
    """List available strategy templates and parameter definitions."""
    return workstation_service.get_strategy_templates()


@router.post("/validate", response_model=StrategyValidateResponse)
async def validate_strategy(req: StrategyValidateRequest) -> StrategyValidateResponse:
    """Validate strategy configuration parameters and check domain rules."""
    return workstation_service.validate_strategy_parameters(req)


@router.get("/active", response_model=StrategyConfigVersionResponse)
async def get_active_strategy_config() -> StrategyConfigVersionResponse:
    """Get the currently active strategy configuration applied to the runtime."""
    return await workstation_service.get_active_strategy_config()


@router.get("", response_model=List[StrategyConfigVersionResponse])
async def list_strategy_configs() -> List[StrategyConfigVersionResponse]:
    """List all saved strategy configurations and versions."""
    return await workstation_service.list_strategy_configs()


@router.post("", response_model=StrategyConfigVersionResponse)
async def create_strategy_config(
    req: StrategyConfigCreateRequest,
) -> StrategyConfigVersionResponse:
    """Create a new strategy configuration."""
    return await workstation_service.create_strategy_config(req)


@router.post("/{config_id}/versions", response_model=StrategyConfigVersionResponse)
async def create_strategy_version(
    config_id: str, req: StrategyConfigVersionRequest
) -> StrategyConfigVersionResponse:
    """Create a new version for an existing strategy configuration."""
    return await workstation_service.create_strategy_version(config_id, req)


@router.post("/{config_id}/activate", response_model=StrategyConfigVersionResponse)
async def activate_strategy_config(config_id: str) -> StrategyConfigVersionResponse:
    """Activate a specific strategy configuration version and apply it immediately to the runtime."""
    try:
        return await workstation_service.activate_strategy_config(config_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


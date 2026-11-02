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


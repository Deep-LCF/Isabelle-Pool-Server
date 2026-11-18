"""POST /api/v1/select_field: the session image to start on so that built theories are reused
(services/image_select.py)."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from server.app.core.input_guards import validate_project_path
from server.app.core.logging import get_logger
from server.app.services.image_select import select_field

router = APIRouter(tags=["images"])
logger = get_logger(__name__)


class SelectFieldRequest(BaseModel):
    imports: List[str] = Field(default_factory=list)
    field: Optional[str] = None
    session_dirs: List[str] = Field(default_factory=list)

    @field_validator("session_dirs")
    @classmethod
    def _dirs(cls, v):
        return [validate_project_path(d) for d in v[:64]]


class SelectFieldResponse(BaseModel):
    field: Optional[str]


@router.post("/api/v1/select_field", response_model=SelectFieldResponse)
async def select_field_endpoint(request: SelectFieldRequest) -> SelectFieldResponse:
    chosen = await asyncio.to_thread(
        select_field, request.imports, request.field, tuple(request.session_dirs))
    if chosen != request.field:
        logger.info("select_field imports=%s proposed=%s chosen=%s",
                    request.imports, request.field, chosen)
    return SelectFieldResponse(field=chosen)

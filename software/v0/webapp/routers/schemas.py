"""Output-schema store: named, selectable structured-output schemas.

You author a neutral JSON Schema once and store it by name; at process time you
pick one and the chosen backend translates it to its native mechanism (Claude
forced tool-call / Ollama `format`). The single `/api/schema` (config router)
remains the active default; this `/api/schemas` store holds named alternatives.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state

router = APIRouter(prefix="/api/schemas", tags=["schemas"])


class SchemaCreate(BaseModel):
    name: str
    schema: dict


class SchemaPatch(BaseModel):
    name: Optional[str] = None
    schema: Optional[dict] = None


@router.get("")
def list_schemas() -> list[dict]:
    return state.get_schemas()


@router.post("")
def create_schema(req: SchemaCreate) -> dict:
    return state.add_schema(req.name, req.schema)


@router.get("/{schema_id}")
def get_schema(schema_id: str) -> dict:
    s = state.get_schema_entry(schema_id)
    if not s:
        raise HTTPException(status_code=404, detail="schema not found")
    return s


@router.put("/{schema_id}")
def put_schema(schema_id: str, patch: SchemaPatch) -> dict:
    s = state.update_schema_entry(schema_id, patch.model_dump(exclude_none=True))
    if not s:
        raise HTTPException(status_code=404, detail="schema not found")
    return s


@router.delete("/{schema_id}")
def del_schema(schema_id: str) -> dict:
    if not state.delete_schema_entry(schema_id):
        raise HTTPException(status_code=404, detail="schema not found")
    return {"deleted": schema_id}

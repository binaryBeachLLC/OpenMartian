"""Capture defaults + the editable decision (structured-output) schema."""

from __future__ import annotations

from fastapi import APIRouter, Body

from openmartian.schema import decision_schema

from .. import state

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_config() -> dict:
    return state.get_config()


@router.put("/config")
def put_config(patch: dict = Body(...)) -> dict:
    return state.update_config(patch)


@router.get("/schema")
def get_schema() -> dict:
    return state.get_config()["decision_schema"]


@router.put("/schema")
def put_schema(schema: dict = Body(...)) -> dict:
    state.update_config({"decision_schema": schema})
    return schema


@router.get("/schema/default")
def get_default_schema() -> dict:
    return decision_schema(["notify"])

"""Recipe store (persisted to data/recipes.json)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


class RecipeCreate(BaseModel):
    name: str
    text: str
    allowed_actions: Optional[list[str]] = None


class RecipePatch(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None
    allowed_actions: Optional[list[str]] = None


@router.get("")
def get_recipes() -> list[dict]:
    return state.get_recipes()


@router.post("")
def post_recipe(req: RecipeCreate) -> dict:
    return state.add_recipe(req.name, req.text, req.allowed_actions)


@router.get("/{recipe_id}")
def get_recipe(recipe_id: str) -> dict:
    r = state.get_recipe(recipe_id)
    if not r:
        raise HTTPException(status_code=404, detail="recipe not found")
    return r


@router.put("/{recipe_id}")
def put_recipe(recipe_id: str, patch: RecipePatch) -> dict:
    r = state.update_recipe(recipe_id, patch.model_dump(exclude_none=True))
    if not r:
        raise HTTPException(status_code=404, detail="recipe not found")
    return r


@router.delete("/{recipe_id}")
def del_recipe(recipe_id: str) -> dict:
    if not state.delete_recipe(recipe_id):
        raise HTTPException(status_code=404, detail="recipe not found")
    return {"deleted": recipe_id}

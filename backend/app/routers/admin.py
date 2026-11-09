from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import admin_user
from ..models import User
from ..services import settings_store
from ..services.titles import rewrite_with_deepseek

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SettingsIn(BaseModel):
    deepseek_api_key: str | None = Field(default=None, max_length=256)  # None = keep current
    clear_deepseek_api_key: bool = False
    deepseek_base_url: str = Field(default="https://api.deepseek.com", max_length=256)
    deepseek_model: str = Field(default="deepseek-chat", max_length=64)


class TestIn(BaseModel):
    title: str = Field(default="伸缩款汽车前挡风玻璃除雾刷后视镜刮水清洁多功能清洁刷K", max_length=200)


def settings_out() -> dict:
    key = settings_store.get("deepseek_api_key")
    return {
        "deepseek_api_key_set": bool(key),
        "deepseek_api_key_masked": settings_store.mask(key),
        "deepseek_base_url": settings_store.get("deepseek_base_url"),
        "deepseek_model": settings_store.get("deepseek_model"),
    }


@router.get("/settings")
def get_settings_(_: User = Depends(admin_user)):
    return settings_out()


@router.put("/settings")
def put_settings(body: SettingsIn, _: User = Depends(admin_user)):
    values = {"deepseek_base_url": body.deepseek_base_url.strip(), "deepseek_model": body.deepseek_model.strip()}
    if body.clear_deepseek_api_key:
        values["deepseek_api_key"] = ""
    elif body.deepseek_api_key:
        values["deepseek_api_key"] = body.deepseek_api_key.strip()
    settings_store.set_many(values)
    return settings_out()


@router.post("/settings/test-deepseek")
def test_deepseek(body: TestIn, _: User = Depends(admin_user)):
    try:
        new = rewrite_with_deepseek(body.title, "", 30, raise_errors=True)
    except Exception as e:
        raise HTTPException(400, f"调用失败：{e}")
    return {"source": body.title, "rewritten": new}

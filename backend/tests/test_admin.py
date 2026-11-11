import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.models import SystemSetting
from app.services import titles
from app.services.template_config import TemplateConfig


def test_first_user_is_admin_and_others_are_not(client):
    assert client.get("/api/auth/me").json()["is_admin"] is True
    with TestClient(app) as c2:
        # Self-registration is closed once the admin exists
        assert client.get("/api/auth/setup").json() == {"registration_open": False}
        r = c2.post("/api/auth/register", json={"username": "customer1", "password": "secret123"})
        assert r.status_code == 403 and "管理员" in r.json()["detail"]
        from .conftest import new_tenant_client
        new_tenant_client(client, c2, "customer1")
        assert c2.get("/api/auth/me").json()["is_admin"] is False
        assert c2.get("/api/admin/settings").status_code == 403
        assert c2.put("/api/admin/settings", json={"deepseek_api_key": "sk-x"}).status_code == 403


def test_deepseek_key_saved_encrypted_masked_and_used(client, monkeypatch):
    r = client.put("/api/admin/settings", json={"deepseek_api_key": "sk-abcdef1234567890",
                                                "deepseek_model": "deepseek-chat"}).json()
    assert r["deepseek_api_key_set"] and r["deepseek_api_key_masked"] == "sk-****7890"
    assert "abcdef" not in str(client.get("/api/admin/settings").json())

    from app.db import SessionLocal
    with SessionLocal() as db:
        stored = db.get(SystemSetting, "deepseek_api_key").value
    assert stored and "sk-abcdef" not in stored  # encrypted at rest

    # Saving other fields without a key keeps the key
    client.put("/api/admin/settings", json={"deepseek_model": "deepseek-chat"})
    assert client.get("/api/admin/settings").json()["deepseek_api_key_set"]

    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["auth"], seen["url"], seen["body"] = headers["Authorization"], url, json
        req = httpx.Request("POST", url)
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": '{"title": "伸缩汽车除雾刷 后视镜清洁工具"}'}}]})

    monkeypatch.setattr(titles.httpx, "post", fake_post)
    out = titles.build_title("伸缩款汽车前挡风玻璃除雾刷K", TemplateConfig())
    assert seen["auth"] == "Bearer sk-abcdef1234567890" and seen["url"].endswith("/chat/completions")
    body = seen["body"]
    assert body["thinking"] == {"type": "disabled"}  # thinking is on by default and costs ~7x
    assert body["model"] == "deepseek-flash"  # legacy "deepseek-chat" maps to the cheapest model
    assert body["messages"][0]["role"] == "system" and "除雾刷" not in body["messages"][0]["content"]
    assert body["messages"][1]["content"].endswith("伸缩款汽车前挡风玻璃除雾刷")  # product data last
    assert body["max_tokens"] <= 200
    assert out.startswith("伸缩汽车除雾刷")

    t = client.post("/api/admin/settings/test-deepseek", json={}).json()
    assert t["rewritten"].startswith("伸缩汽车除雾刷")

    client.put("/api/admin/settings", json={"clear_deepseek_api_key": True})
    assert client.get("/api/admin/settings").json()["deepseek_api_key_set"] is False
    assert client.post("/api/admin/settings/test-deepseek", json={}).status_code == 400

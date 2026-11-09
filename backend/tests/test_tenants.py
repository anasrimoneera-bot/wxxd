from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.main import app


def login(username, password="secret123"):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    return c, r


def test_admin_manages_customers(client):
    t = client.post("/api/admin/tenants", json={"username": "cust1", "password": "secret123",
                                                "company": "客户一", "months": 1}).json()
    assert t["status"] == "active" and t["days_left"] >= 29 and t["username"] == "cust1"

    c, r = login("cust1")
    assert r.status_code == 200
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    assert c.get("/api/stores").status_code == 200
    assert c.get("/api/admin/tenants").status_code == 403  # customers can't see admin

    # expire -> existing session and new logins are refused with a clear message
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert client.put(f"/api/admin/tenants/{t['id']}", json={"expires_at": yesterday}).json()["status"] == "expired"
    r2 = c.get("/api/stores")
    assert r2.status_code == 401 and "到期" in r2.json()["detail"]
    assert "到期" in login("cust1")[1].json()["detail"]

    # extend 3 months from today -> works again
    e = client.post(f"/api/admin/tenants/{t['id']}/extend", json={"months": 3}).json()
    assert e["status"] == "active" and e["days_left"] >= 89
    assert login("cust1")[1].status_code == 200

    # disable / enable
    client.put(f"/api/admin/tenants/{t['id']}", json={"active": False})
    assert "停用" in login("cust1")[1].json()["detail"]
    client.put(f"/api/admin/tenants/{t['id']}", json={"active": True})

    # reset password
    client.post(f"/api/admin/tenants/{t['id']}/reset-password", json={"password": "newpass99"})
    assert login("cust1", "newpass99")[1].status_code == 200

    # list, search, filter; admin's own tenant can't be disabled
    rows = client.get("/api/admin/tenants").json()
    assert {r["username"] for r in rows} == {"owner1", "cust1"}
    assert [r["username"] for r in client.get("/api/admin/tenants", params={"q": "客户"}).json()] == ["cust1"]
    me = [r for r in rows if r["is_admin"]][0]
    assert client.put(f"/api/admin/tenants/{me['id']}", json={"active": False}).status_code == 400


def test_auto_migration_adds_missing_columns(tmp_path):
    from app.migrate import upgrade
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as conn:  # a tenant table from the previous release
        conn.execute(text("CREATE TABLE tenant (id INTEGER PRIMARY KEY, name VARCHAR(64), created_at DATETIME)"))
        conn.execute(text("INSERT INTO tenant (id, name, created_at) VALUES (1, 'old', '2026-01-01')"))
    added = upgrade(eng)
    assert {"tenant.active", "tenant.expires_at", "tenant.note"} <= set(added)
    cols = {c["name"] for c in inspect(eng).get_columns("tenant")}
    assert {"active", "expires_at", "note"} <= cols
    with eng.connect() as conn:
        assert conn.execute(text("SELECT active, note FROM tenant WHERE id=1")).one() == (1, "")
    assert upgrade(eng) == []  # idempotent

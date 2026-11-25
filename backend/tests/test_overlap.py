"""店铺商品相同率: of a store's products, at most 40% (系统设置) may also be in any one other store."""
from app.db import SessionLocal
from app.models import PublishedRecord, Store

from .conftest import make_package
from .test_flow import setup_store, upload


def _keys(client, task_id):
    return {i["source_product_id"] for i in client.get(f"/api/tasks/{task_id}/items", params={"size": 500}).json()["items"]}


def test_overlap_between_stores_at_most_40_percent(client):
    assert client.get("/api/admin/settings").json()["max_overlap_pct"] == 40
    assert upload(client, {f"{i}.json": make_package(str(i), f"商品{i}") for i in range(1, 21)})["created"] == 20
    a, _ = setup_store(client, appid="wxstoreA0001")
    b, _ = setup_store(client, appid="wxstoreB0001")
    with SessionLocal() as db:  # store A already has products 1-10
        db.add_all([PublishedRecord(store_id=a["id"], source_key=f"wechat:{i}", title_hash=f"h{i}") for i in range(1, 11)])
        db.commit()
    # store B, all 20: the 10 A does not have, then of A's 10 only as many as keep 相同率 <= 40%
    t = client.post("/api/tasks", json={"store_ids": [b["id"]], "platform": "wechat"}).json()[0]
    keys = _keys(client, t["id"])
    shared = keys & {str(i) for i in range(1, 11)}
    assert {str(i) for i in range(11, 21)} <= keys and len(shared) == 4  # 4 of 14 (B) and 4 of 10 (A): <= 40%
    assert "相同率≤40% 跳过 6 个" in t["scope"]

    # a third store: every product is in A or B now, so it cannot be <= 40% the same as both: refused
    c, _ = setup_store(client, appid="wxstoreC0001")
    r = client.post("/api/tasks", json={"store_ids": [c["id"]], "platform": "wechat", "random_count": 5})
    assert r.status_code == 400 and "相同率" in r.json()["detail"]
    # 0 = off
    client.put("/api/admin/settings", json={"max_overlap_pct": 0})
    t2 = client.post("/api/tasks", json={"store_ids": [c["id"]], "platform": "wechat"}).json()[0]
    assert t2["total"] == 20 and "相同率" not in t2["scope"]


def test_two_stores_in_one_request_share_the_limit(client):
    assert upload(client, {f"{i}.json": make_package(str(i), f"货{i}") for i in range(1, 11)})["created"] == 10
    a, _ = setup_store(client, appid="wxstoreA0002")
    b, _ = setup_store(client, appid="wxstoreB0002")
    tasks = client.post("/api/tasks", json={"store_ids": [a["id"], b["id"]], "platform": "wechat"})
    # A gets all 10; B would be 100% the same as A: nothing left for it
    assert tasks.status_code == 400 and "相同率 40%" in tasks.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Store).count() == 2


def test_new_store_takes_a_little_from_many_stores(client):
    """A product from each of many stores is fine: each pair has only 1 in common."""
    assert upload(client, {f"{i}.json": make_package(str(i), f"品{i}") for i in range(1, 11)})["created"] == 10
    stores = [setup_store(client, appid=f"wxmany{i:06d}")[0] for i in range(11)]
    with SessionLocal() as db:  # stores 0..9 have product i+1 and 4 own products each
        for i in range(10):
            db.add(PublishedRecord(store_id=stores[i]["id"], source_key=f"wechat:{i + 1}", title_hash=f"x{i}"))
            db.add_all([PublishedRecord(store_id=stores[i]["id"], source_key=f"other:{i}-{k}", title_hash=f"o{i}{k}") for k in range(4)])
        db.commit()
    t = client.post("/api/tasks", json={"store_ids": [stores[10]["id"]], "platform": "wechat"}).json()[0]
    assert t["total"] == 10  # 1 of 10 in common with each store: 10% (and 20% of each of theirs)

"""打单: 电子面单 取号 → print_info for the 打印组件 → 打印成功通知 + 订单发货; 面单 PDF → receiver address."""
from app.db import SessionLocal
from app.models import Store
from app.services import label_ocr, order_sync

from .test_flow import setup_store
from .test_orders import ShipWeChat, detail

OID = "3740651248588075264"
# the lines OCR reads from the user's 极兔 label (text of the sample 面单 PDF)
LABEL = ['Q956025', '标准', 'J&T极兔速递', '快递', '2026/11/1916:51第1/1次JT4161059381738', 'JT4161059381738', '410,Q764-00,369',
         'JT4161059381738', '嘉兴极兔极嘉分拨', 'T4161059381', '北辰金邸', '1**********转1180', '隐私号', '王** *******4921',
         '浙江省嘉兴市嘉善县天凝', 'JT4161059381738', 'JT4161059381738', '镇东方路218号（北辰金', '1738', '邸）11-1-302室',
         '杨*芸.15******547.山西', '省晋中市榆社县北寨村', '四队三排东户103号', '商品总数量共1件', '提高警惕',
         '男士防臭袜中筒袜秋冬季涤棉袜子吸汗透气百搭潮流男袜拼色男袜【超', '值10双装，均码(39-44）361393567821件',
         f'订单号：{OID}', '谨防诈骗', '微信小店']


class PrintWeChat(ShipWeChat):
    calls = []

    def ewaybill_accounts(self):
        return [{"acct_id": "A9", "delivery_id": "JTSD", "shop_id": "S1", "site_info": {"site_code": "571", "site_name": "嘉兴网点"},
                 "sender_address": {"province": "山西省", "city": "晋中市", "county": "榆社县", "street": "", "address": "北寨村"}}]

    def ewaybill_templates(self):
        return [{"delivery_id": "JTSD", "default_template_id": "T1", "template_list": [{"template_id": "T1", "template_name": "一联单"}]}]

    def ewaybill_precreate(self, body):
        PrintWeChat.calls.append(("precreate", body))
        return "EW100"

    def ewaybill_create(self, body):
        PrintWeChat.calls.append(("create", body))
        return {"ewaybill_order_id": "EW100", "waybill_id": "JT4161059381738", "print_info": "PRINT-INFO"}

    def ewaybill_print_content(self, eid, tid):
        return "PRINT-AGAIN"

    def ewaybill_printed(self, eid, delivery_id, waybill_id, re_print):
        PrintWeChat.calls.append(("printed", eid, waybill_id, re_print))


def test_parse_label_from_ocr_lines():
    lab = label_ocr.parse_label(LABEL)
    assert lab["surname"] == "王" and lab["last4"] == "4921" and lab["order_id"] == OID
    assert lab["address"] == "浙江省嘉兴市嘉善县天凝镇东方路218号（北辰金邸）11-1-302室"  # side waybill numbers left out


def test_print_waybill_then_ship(client, monkeypatch):
    from app.routers import stores
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", PrintWeChat)
    PrintWeChat.calls, ShipWeChat.sent, ShipWeChat.state = [], [], {}
    with SessionLocal() as db:
        order_sync.upsert(db, db.get(Store, store["id"]), detail(OID, qty=1))
        db.commit()
    o = client.get("/api/orders").json()["items"][0]
    opts = client.get("/api/orders/print/options", params={"store_id": store["id"]}).json()
    acct = opts["accounts"][0]
    assert acct["delivery_id"] == "JTSD" and opts["templates"]["JTSD"]["default"] == "T1"
    sender = {"name": "杨芸", "mobile": "15000000547", "province": "山西省", "city": "晋中市", "county": "榆社县", "address": "北寨村"}
    body = {"order_ids": [o["id"]], "acct_id": "A9", "delivery_id": "JTSD", "shop_id": "S1", "site_code": "571",
            "template_id": "T1", "sender": sender}
    r = client.post("/api/orders/print/waybills", json=body).json()["results"][0]
    assert r["ok"] and r["waybill_id"] == "JT4161059381738" and r["print_info"] == "PRINT-INFO" and not r["re_print"]
    create = next(c[1] for c in PrintWeChat.calls if c[0] == "create")
    assert create["ewaybill_order_id"] == "EW100" and create["ec_order_list"][0]["ec_order_id"] == int(OID)
    assert create["receiver"]["province"] == "广东省" and create["sender"]["name"] == "杨芸"
    # printing again (补打): the same 面单, its print data again
    again = client.post("/api/orders/print/waybills", json=body).json()["results"][0]
    assert again["re_print"] and again["print_info"] == "PRINT-AGAIN"
    # the 打印组件 printed it: 打印成功通知 + 订单发货 with the waybill number
    d = client.post("/api/orders/print/done", json={"items": [{"id": o["id"]}], "ship": True}).json()["results"][0]
    assert d["ok"] and ("printed", "EW100", "JT4161059381738", False) in PrintWeChat.calls
    assert ShipWeChat.sent[0][1][0]["waybill_id"] == "JT4161059381738" and ShipWeChat.sent[0][1][0]["delivery_id"] == "JTSD"
    assert client.get("/api/orders/print/options", params={"store_id": store["id"]}).json()["sender"]["name"] == "杨芸"


def test_label_pdf_import(client, monkeypatch):
    store, _ = setup_store(client)
    with SessionLocal() as db:
        order_sync.upsert(db, db.get(Store, store["id"]), detail(OID, qty=1))
        db.commit()
    monkeypatch.setattr(label_ocr, "page_lines", lambda data, max_pages=200: [LABEL, ["空白页"]])
    r = client.post("/api/orders/labels/import", files={"file": ("面单.pdf", b"%PDF-1.4", "application/pdf")}).json()
    assert r["ok"] == 1 and r["failed"] == 1
    got = r["results"][0]
    assert got["order_id"] == OID and got["name"] == "王" and got["address"].startswith("浙江省嘉兴市嘉善县")
    assert len(got["phone"]) == 11 and got["phone"][0] == "1" and got["phone"][1] in "3456789" and got["phone"].endswith("4921")
    o = client.get("/api/orders").json()["items"][0]
    assert o["label_address"]["address"] == got["address"]  # shown in the order's buyer details


def test_print_permission_count_reset_and_auto_label(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import stores

    from .conftest import new_tenant_client
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", PrintWeChat)
    PrintWeChat.calls, ShipWeChat.sent, ShipWeChat.state = [], [], {}
    with SessionLocal() as db:
        order_sync.upsert(db, db.get(Store, store["id"]), detail(OID, qty=1))
        db.commit()
    with TestClient(app) as other:
        new_tenant_client(client, other, "printer2")
        tid = next(t["id"] for t in client.get("/api/admin/tenants").json() if t["username"] == "printer2")
        client.post(f"/api/admin/tenants/{tid}/stores", json={"store_ids": [store["id"]]})
        assert other.get("/api/auth/me").json()["can_print"] is False
        assert other.get("/api/orders/print/options", params={"store_id": store["id"]}).status_code == 403
        t = client.put(f"/api/admin/tenants/{tid}/print", json={"enabled": True}).json()
        assert t["print_enabled"] and t["print_count"] == 0 and other.get("/api/auth/me").json()["can_print"]
        oid = other.get("/api/orders").json()["items"][0]["id"]
        sender = {"name": "杨芸", "mobile": "15000000547", "province": "山西省", "city": "晋中市", "county": "榆社县", "address": "北寨村"}
        body = {"order_ids": [oid], "acct_id": "A9", "delivery_id": "JTSD", "shop_id": "S1", "template_id": "T1", "sender": sender}
        assert other.post("/api/orders/print/waybills", json=body).json()["results"][0]["ok"]
        d = other.post("/api/orders/print/done", json={"items": [{"id": oid}]}).json()["results"][0]
        assert d["ok"] and not ShipWeChat.sent  # 打单 only prints: no 发货
        other.post("/api/orders/print/done", json={"items": [{"id": oid, "re_print": True}]})
        t = next(x for x in client.get("/api/admin/tenants").json() if x["id"] == tid)
        assert t["print_count"] == 1 and t["print_reprints"] == 1
        logs = client.get(f"/api/admin/tenants/{tid}/print/logs").json()
        assert logs[0]["username"] == "printer2" and logs[0]["order_id"] == OID
        t = client.post(f"/api/admin/tenants/{tid}/print/reset").json()
        assert t["print_count"] == 0 and t["print_reprints"] == 0 and t["print_reset_at"]

        # after printing: the 打印组件's preview image is read → the receiver (not the store's own sender)
        sample = [l for l in LABEL]
        monkeypatch.setattr(label_ocr, "image_lines", lambda data: sample)
        r = other.post(f"/api/orders/{oid}/label-image", files={"file": ("p.png", b"img", "image/png")}).json()
        assert r["label_address"]["name"] == "王" and r["label_address"]["address"].startswith("浙江省嘉兴市")

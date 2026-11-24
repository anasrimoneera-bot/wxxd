"""发布配置 → 运费模板: create / edit the store's freight templates through the WeChat API."""
from tests.conftest import FakeWeChat

from .test_flow import setup_store


def test_create_and_edit_freight_template(client):
    store, _ = setup_store(client)
    sid = store["id"]
    FakeWeChat.freight_saved = []
    opts = client.get("/api/stores/freight-options").json()
    assert len(opts["provinces"]) == 31 and opts["send_times"][0]["id"] == "SendTime_TWENTYFOUR_HOUR"

    # 新建: 满 2 件包邮, otherwise ¥6 first piece + ¥2 each; 新疆 not delivered
    r = client.post(f"/api/stores/{sid}/freight-templates", json={
        "name": "满2件包邮", "address_id": "222", "shipping_method": "CONDITION_FREE", "first_price": 6, "second_price": 2,
        "condition": "piece", "min_piece": 2, "not_send_provinces": ["新疆维吾尔自治区"]}).json()
    assert r == {"id": "555", "name": "满2件包邮"}
    kind, tpl = FakeWeChat.freight_saved[-1]
    assert kind == "add" and tpl["address_info"]["county_name"] == "朝阳区" and tpl["delivery_type"] == "EXPRESS"
    assert tpl["all_freight_calc_method"]["freight_calc_method_list"] == [
        {"is_default": True, "first_val_amount": 1, "first_price": 600, "second_val_amount": 1, "second_price": 200}]
    cond = tpl["all_condition_free_detail"]["condition_free_detail_list"][0]
    assert cond["min_piece"] == 2 and cond["valuation_flag"] == 1 and len(cond["address_infos"]) == 30
    assert tpl["not_send_area"]["address_infos"] == [{"province_name": "新疆维吾尔自治区"}]
    assert client.post(f"/api/stores/{sid}/freight-templates", json={"name": "x"}).status_code == 400  # 发货地址

    # 编辑: the form shows the template; saving keeps the 指定地区运费 set in the 小店后台
    f = client.get(f"/api/stores/{sid}/freight-templates/111").json()
    assert (f["shipping_method"], f["first_price"], f["not_send_provinces"], f["region_rules"]) == ("NO_FREE", 5, ["西藏自治区"], 1)
    client.put(f"/api/stores/{sid}/config", json={"freight_template_id": "111", "freight_template_name": "全国包邮(偏远地区除外)"})
    r = client.put(f"/api/stores/{sid}/freight-templates/111", json={**f, "name": "不包邮", "first_price": 8, "address_id": ""}).json()
    kind, tpl = FakeWeChat.freight_saved[-1]
    assert kind == "update" and tpl["template_id"] == "111" and "create_time" not in tpl
    calc = tpl["all_freight_calc_method"]["freight_calc_method_list"]
    assert calc[0]["first_price"] == 800 and calc[1]["first_price"] == 2000 and tpl["address_info"] == {"province_name": "北京市"}
    assert client.get("/api/stores").json()[0]["freight_template_name"] == "不包邮"  # the new name in 发布配置
    assert client.put(f"/api/stores/{sid}/freight-templates/111", json={**f, "not_send_provinces": ["火星"]}).status_code == 400

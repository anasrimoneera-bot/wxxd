# 蓝鲸科技 · 微信小店联盟选品工具

一个**合规**的微信小店商品选品工具：数据全部来自微信小店官方「联盟带货」开放接口
（`api.weixin.qq.com`），**不注入微信进程、不读取微信内存、不修改系统代理**。

## 功能
- 分页拉取联盟可推广商品（全量）
- 查看单品完整详情
- 关键词/价格/佣金筛选选品
- 导出 Excel
- 批量下载主图、视频

## 数据来源
官方联盟接口，需要带货身份（达人 / 机构 / 服务商）的 `access_token`：
- 商品列表：`POST /channels/ec/league/item/list/get`
- 商品详情：`POST /channels/ec/league/item/get`

没有凭证时可用 `--demo` 样例数据模式预览界面与导出。

## 运行
```bash
pip install -r requirements.txt
# 命令行预览（样例数据，无需凭证）
python -m lanjing.cli --demo --export out.xlsx
# 图形界面
python -m lanjing.app
```

## 合规说明
本工具不含任何进程注入、内存读取或调用微信未公开函数的能力，采集范围仅限官方接口
按你的带货权限返回的数据。请遵守微信小店联盟的接入协议与数据使用规范。

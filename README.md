# 微信小店助手 (WeChat Store Assistant)

A multi-tenant web app for WeChat Store merchants. It currently covers the
cloud-publishing workflow:

| Menu | What it does |
|---|---|
| 店铺管理 Stores | Connect a store with its own AppID + AppSecret (encrypted at rest); connection test; shop name, logo, `gh_` ID and status read from WeChat. |
| 商品云库 Product library | Upload collected product data packages (`.json` / `.txt`, or a `.zip` of them; up to 500 per upload). Re-uploading the same source product updates it. Platform counts, search by name / IDs / platform / group / price, groups, move, batch delete, preview, SKU view, Excel export. |
| 发布配置 Publish config | Per store: freight template and return address (read live from the store) and a publish template (上货模板). Bulk configuration. "设置上货商品" picks a platform and group and creates one task per selected store. |
| 上货模板 Publish template | Delivery time, price formula and rounding, listing position, title prefix/suffix, keyword replacement, title/SKU banned words, lure-SKU filter, freight insurance, 7-day return, duplicate filter, price filter, fixed price ending, 30-char truncation, DeepSeek title rewrite with keyword dedupe, short title, video upload, 6 main-image watermark styles, damage return / 假一赔三 / exchange, main/detail image deletion, stock rule, auto-fill required attributes, custom attributes with units, automatic category application (deposit cap 5,000 元). |
| 任务列表 Tasks | Progress (success / failed / remaining), status, worker, per-product results with WeChat category, WeChat product ID and failure reason; Excel export; retry failed items. |
| 子账号管理 Sub-accounts | Owner creates operator accounts (everything except sub-account management). |
| 系统设置 System settings | Platform admin only: DeepSeek API key (encrypted, shown masked), endpoint and model, with a "测试改写" test button. Workers pick up changes within 30 s. |

The design document is in [`docs/PROPOSAL.md`](docs/PROPOSAL.md).

## Architecture

- `backend/` is FastAPI + SQLAlchemy (MySQL 8 in production, SQLite for development).
  - `app/services/parser.py` reads data packages (`data.data` format).
  - `app/services/publisher.py` applies a publish template and calls the WeChat API.
  - `app/services/wechat.py` holds **every** WeChat endpoint in one place.
  - `app/worker.py` is the publish worker. Run as many copies as you like: each claims one product at a time (`SELECT … FOR UPDATE SKIP LOCKED`), and a crashed worker's item is re-queued after 10 minutes.
- `frontend/` is Vue 3 + TypeScript + Element Plus, built into the API image and served by it.

## Deploy on the Volcano Engine server (Ubuntu 22.04)

1. Install Docker: `curl -fsSL https://get.docker.com | sh`
2. Copy the repo to the server, then create `.env` from `.env.example` and fill in `MYSQL_*`, `JWT_SECRET`, `SECRET_KEY_B64` and `SERVER_PUBLIC_IP` (your EIP). **Back up `SECRET_KEY_B64`**: it decrypts every store's AppSecret.
3. Start: `docker compose up -d --build`. This runs MySQL, the API on 127.0.0.1:8000 and 3 workers.
4. Add a DNS A record `wxxd` → the server EIP, then add `deploy/nginx.conf` as an extra nginx site and run `certbot --nginx -d wxxd.b2bsxlj.com`. The site will be at `https://wxxd.b2bsxlj.com`.
5. Open the site and register. **The first account registered becomes the platform admin** and sees 系统设置, where you enter the DeepSeek API key. Then set `ALLOW_REGISTER=false` if you create customer accounts yourself. To make another account admin: `docker compose exec api python -m app.admin_cli grant <username>`.
6. Every merchant adds `SERVER_PUBLIC_IP` to their store's API IP whitelist before connecting.

Scale workers with `docker compose up -d --scale worker=6`. Each worker pauses `WORKER_ITEM_INTERVAL_SECONDS` between products to stay under WeChat rate limits.

## Development

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
pytest                          # uses SQLite; TEST_DATABASE_URL=mysql+pymysql://... to test on MySQL
uvicorn app.main:app --reload   # API on :8000
python -m app.worker --name worker-001
cd ../frontend && npm install && npm run dev   # UI on :5173, proxies /api to :8000
```

## Before going live: check against the official 微信小店 API docs on a test store

These are isolated in `backend/app/services/wechat.py` and `publisher.py`, marked `VERIFY`:

- **Account categories and category application:** `category/list/get`, `category/add` (endpoint names and body shape).
- **Category detail:** the attribute list shape (`attr.product_attr_list`, `is_required`) and the deposit field and its unit.
- **`product/add` fields:** `extra_service` names (damage return, 假一赔三, exchange) and presale fields.
- **Main-image video:** the product API field for it is not confirmed, so videos are currently **not attached**. The task result says "主图视频待接口确认后上传".
- **Short title:** the maximum length (currently 10 characters).

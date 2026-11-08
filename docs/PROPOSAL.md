# WeChat Store Operations Console — Technical Proposal

> A multi-tenant web app for WeChat Store (微信小店) merchants: product upload, shipping-template management, order fulfillment & logistics, plus bulk sourcing from 1688.com. Self-hosted on your Volcano Engine (火山引擎) server.

Status: draft v0.1 · 2026-11-08

---

## 1. Goals and scope

| # | Module | What it does |
|---|--------|--------------|
| M1 | Store connection | Each user binds their **own** WeChat Store credentials; the system calls the official API on their behalf. |
| M2 | Product management | Create/edit products, upload images, map categories & attributes, SKUs, submit for review, list/delist, bulk import. |
| M3 | Shipping templates | Create, edit, and delete freight templates (运费模板); assign a template to one product or many at once. |
| M4 | Orders & fulfillment | Sync orders, filter by status, ship (single or bulk), print/export picking lists, handle after-sales. |
| M5 | Logistics | Courier list, tracking-number entry/import, shipping status, exceptions (e.g. unshipped for 48h). |
| M6 | 1688 sourcing | Bulk keyword or image search on 1688, compare suppliers, link a 1688 offer to a store product, one-click "draft product from offer". |
| M7 | Category applications (类目申请) | Sync the full WeChat Store category tree (all levels), filter by deposit and qualification needs, and apply for many or **all** eligible categories in one click, across one or several stores. Track application results. |

**Out of scope for v1:** live-stream tooling, ads, CRM/marketing, automatic purchase placement on 1688 (can come in phase 3).

**Ground rule (same as the previous version of this repo):** use **official APIs only**. No scraping of the WeChat back office or 1688 pages. That keeps accounts safe and the product sellable.

---

## 2. Key external dependencies (check these first)

### 2.1 WeChat Store API (微信小店 / 视频号小店 API)
Docs: `developers.weixin.qq.com` → 微信小店 → 服务端 API.

| Capability | Representative endpoints (check names against current docs) |
|---|---|
| Access token | `POST /cgi-bin/stable_token` (appid + appsecret) |
| Categories / brands / qualifications | `/channels/ec/category/all`, `/channels/ec/category/detail` |
| Category applications | List the categories the store may apply for and those already granted, submit a category application (with qualification files when needed), query its audit status, cancel it (see the "类目" section of the 微信小店 API: available sub-categories, apply/add category, get audit, cancel audit, get account categories) |
| Image upload | `/channels/ec/basics/img/upload` (returns a WeChat-hosted URL; external URLs are not accepted directly) |
| Products | `/channels/ec/product/add`, `/update`, `/get`, `/list/get`, `/listing`, `/delisting` |
| Freight templates | `/channels/ec/merchant/addfreighttemplate`, `updatefreighttemplate`, `getfreighttemplatelist`, `getfreighttemplatedetail` |
| Orders | `/channels/ec/order/list/get`, `/channels/ec/order/get` |
| Shipping | `/channels/ec/order/deliverycompanylist/get`, `/channels/ec/order/delivery/send` |
| After-sales | `/channels/ec/aftersale/getaftersalelist`, `getaftersaleorder`, accept/reject APIs |
| Event push | Message push to your callback URL (new order, paid, cancelled, after-sale, product audit result) |

**Two ways to connect each user** (pick one for v1):

| Option | How it works | Pros | Cons |
|---|---|---|---|
| **A. Self-managed credentials** (matches your request) | User pastes their store's AppID + AppSecret into the console. User adds your server's public IP to the store's **API IP whitelist**. | Fast to build; no WeChat review for you. | Users must handle secrets and the whitelist; lower trust. |
| B. Third-party platform (第三方平台 / 服务商) | You register a service-provider app on the WeChat Open Platform; merchants authorize by scanning a QR code; you get `authorizer_access_token`. | Best UX; no secrets handled by users; scales commercially. | Needs a company entity, platform review, and the component-ticket flow. |

**Recommendation:** ship **A** in phase 1, and design the token layer as an interface so you can add **B** later without touching the business modules.

### 2.2 1688 Open Platform (open.1688.com)
- Bulk sourcing must go through the **1688 Open Platform**. Register an app and apply for the product search capabilities: keyword search, **image search (以图搜款)**, offer detail, and the distribution/cross-border supply APIs (分销/跨境 solutions). Exact API packages and quotas depend on the solution you are approved for. **Start this application on day 1**, because approval takes time and decides which features you can offer.
- Requests are signed with an AppKey/AppSecret plus OAuth for the account-bound APIs.
- If approval is delayed: an interim fallback is a **licensed** third-party data provider that resells 1688 search APIs. Do not build your own scraper.

### 2.3 Volcano Engine (火山引擎) infrastructure
- **Fixed public egress IP.** WeChat's IP whitelist requires one. Use an EIP on the ECS instance, or a NAT gateway with an EIP if you scale to several nodes.
- **ICP filing (备案).** Required if the server is in mainland China and serves a domain. You also need HTTPS, since the WeChat callback URL must be a reachable domain.
- *Note:* Volcano Engine (mainland) and BytePlus (international) are separate consoles and accounts. Mainland Volcano Engine regions are best for WeChat latency and compliance.

---

## 3. Architecture

```
                ┌──────────────────────────── Volcano Engine VPC ─────────────────────────────┐
 Browser ──HTTPS──► CLB / Nginx ──► Web (Vue 3 SPA, static)                                     │
                │                 └─► API service (REST, auth, tenants)  ──► MySQL / veDB      │
                │                         │            ▲                  ──► Redis (tokens,   │
                │                         ▼            │                       locks, cache)  │
                │                   Job queue (Redis/BullMQ or Celery)                          │
                │                         │                                                     │
                │          ┌──────────────┼───────────────┬──────────────────┐                  │
                │          ▼              ▼               ▼                  ▼                  │
                │   WeChat Gateway   Order Sync      1688 Gateway      Media worker            │
                │   (token mgr,      worker          (sign, quota,     (download→compress→     │
                │    rate limit,     (poll + push)    cache)            upload to WeChat)      │
                │    retries)                                                                   │
                │          │  ▲ callback (/wx/notify/{tenant})                ▼                 │
                └──────────┼──┼──────────────────────────────────────── TOS (object storage) ──┘
                     EIP (whitelisted)
                           ▼
              api.weixin.qq.com              gw.open.1688.com
```

### 3.1 Recommended stack
| Layer | Choice | Why |
|---|---|---|
| Frontend | Vue 3 + TypeScript + Element Plus (or Ant Design Vue) | Large Chinese-language ecosystem; good admin-table components. |
| Backend | **Python FastAPI** (or NestJS if your team prefers TS) | Async HTTP for many tenants; quick to build. |
| Jobs | Celery + Redis (Python) / BullMQ (Node) | Bulk upload, bulk ship, order sync, and 1688 batch search are all long-running. |
| DB | MySQL 8 (Volcano Engine RDS for MySQL, or self-hosted) | Relational order and product data. |
| Cache/locks | Redis | access_token cache, distributed locks, rate limiting. |
| Files | Volcano Engine **TOS** | Product images and exported spreadsheets. |
| Deploy | Docker Compose on 1 ECS (phase 1) → VKE (K8s) later | Simple start. |
| Observability | Volcano Engine TLS log service + Prometheus/Grafana | Track API error codes per tenant. |

### 3.2 Multi-tenancy and security
- Every business table has a `tenant_id`; it is enforced in the ORM layer.
- **AppSecret encryption.** Use AES-GCM with a master key from Volcano Engine **KMS**. Never return the AppSecret to the browser, and never log it.
- **Token manager.** Keep one `stable_token` per tenant in Redis with a TTL slightly under its expiry. Refresh under a Redis lock so concurrent workers don't overwrite each other. On `40001`/`42001`, refresh once and retry.
- **Roles.** Owner / Operator / Warehouse (the warehouse role can only ship). Every write is recorded in an audit log.
- **Callbacks.** Verify signatures and decrypt in safe mode (AES). Make handlers idempotent by deduplicating on event ID + order ID.

---

## 4. Module design

### M1 Store connection
- Wizard: enter AppID/AppSecret → "Test connection" (fetch token + shop info) → show the server IP to whitelist → set the callback URL/Token/EncodingAESKey.
- Health panel: token status, last sync time, API quota/errors from the last 24h.

### M2 Products
- **Editor:** title, category cascade (cached from the category API per tenant, refreshed daily), required attributes and qualifications, main and detail images, SKU matrix (spec × price × stock × SKU code), freight template, after-sale settings.
- **Image pipeline:** source URL or upload → TOS → resize/compress to WeChat limits → `img/upload` → store the WeChat URL. The original and WeChat URLs are kept as a pair so re-uploads are skipped.
- **Bulk import:** Excel template or "from 1688 offers" (M6). Each row becomes a job with per-row status (draft / submitted / in review / rejected (reason) / live).
- **Audit:** subscribe to the product-audit push event and also poll as a fallback.
- **Local model:** `product` (local draft + WeChat product_id), `sku`, `product_source_link` (→ 1688 offer).

### M2a Bulk import from collected product JSON

The product data you supplied (sample batch `选品11.7.zip`, 155 files) holds WeChat Store product detail responses captured from other shops' listings. M2a turns these files into **drafts in the user's own store**. The app only *imports* files the user uploads. It does not include a collector or scraper.

**What the sample contains**

| Shape | Files | How to read it |
|---|---|---|
| A. `{"data":{"data":"<JSON string>"},"errno","errMsg","errorCode"}` | 144 | Parse `data.data` as JSON → `.product` |
| B. `{"data":"<JSON string>"}` | 2 | Parse `data` as JSON → `.product` |
| C. Bare `product` object (`product_key`, `info`, `product_skus`) | 2 | Use as is |
| D. Bare `info` object, snake_case or camelCase (`headImg`, `subTitle`) | 2 | Normalise key case, then use as `info` |
| E. Page-state dump (`data` is a list of `[path, value]` tuples) | 5 | Unsupported in v1. Flag as "re-collect in format A". |

- **Spread:** 15 source shops, all `errcode: 0`. SKUs per product range from 1 to 31 (most have 1–3).
- **Top-level categories:** concentrated in a few `cat_id`s (`1421`, `1142`, `1453`, `1701`, `6263`, …).
- **Prices:** in **fen** (`2990` = ¥29.90).

**Field mapping → `/channels/ec/product/add`**

| Source (`product.info` / `product_skus[]`) | Target | Rule |
|---|---|---|
| `title`, `short_title` | `title`, `short_title` | Then **rewrite** (see below). |
| `head_img[]`, `detail.detail_img[]`, `product_sku_info.thumb_img` | `head_imgs`, `desc_info.imgs`, `skus[].thumb_img` | Download → TOS → `img/upload`. Never submit the source URLs. Main images 3–9, detail ≤ limit. |
| `category[].cat_id` (3–4 levels) | `cats_v2` | Same WeChat category IDs. Check against the shop's opened categories (**M7**). If a category isn't open: queue an application, or remap to an open category. |
| `param[].param_v2` (`attr_id`, `attr_value_id`, `attr_name`, `attr_value_name`, `is_custom_*`) | `attrs` | Re-validate against `category/detail` for the target leaf. Required attributes that are missing are shown in the editor. |
| `product_skus[].product_sku_info.sale_param[].param_v2` | `skus[].sku_attrs` | Spec name/value pairs. |
| `product_skus[].product_sku_info.sale_price` | `skus[].sale_price` | Apply the user's **pricing rule**, not the source price. |
| `stock_info.stock_num` | `skus[].stock_num` | **Ignore.** Use the user's default stock, or the stock from the linked supplier (M6). |
| `brand_id` (`2100000000` = no brand) | `brand_id` | Keep "no brand" only. Any other brand requires the user's own brand authorisation. |
| `extra_service` (7-day return, etc.) | `extra_service` | Use the user's shop defaults. |
| `express_info.template_id` | `express_info.template_id` | **Replace** with the user's template (M3). The source template belongs to another shop. |

**Always dropped:**
- Identifiers of the source shop: `product_key.appid`, `product_id`, `sku_id`, `sku_snap_id`, `after_sale_address_id`, `last_update_useruin`, `copy_from_spu_id`.
- Their sales, visit and score data: `total_sold_num`, `total_order_num`, `total_visit_num`, `product_score`.
- Marketing prices (`sku_list.discounted_price`, coupons) and their qualification files (`qualification_list`).

The raw file is kept in TOS for traceability only.

**Import flow**
1. Upload a `.zip` or several `.json` files. A parser job detects the shape of each file (A–E) and normalises it to an internal `ImportedProduct`.
2. **Pre-check per product:** category opened? required attributes present? images downloadable? a draft with the same title/main image already exists (dedupe by image hash)?
3. **Transform** with user rules:
   - Pricing formula, default stock, freight template, after-sale defaults.
   - Title rewrite: banned/brand-word filter, a template, and optionally a model (Volcano Engine Ark / Doubao, or a model of your choice).
   - Image processing: crop or replace the first image, strip logos/watermarks *of the user's own templates only*.
4. **Review grid:** each row shows its status (ready / needs category / needs attributes / failed). Bulk-edit, then **submit selected**, which runs through the same rate-limited job as M2.
5. A `.xlsx` report covers every file, including the unsupported ones.

**Risks with collected listings: please read**
- **Platform rules.** WeChat Store's rules penalise listings that copy another shop's images or text (搬运/盗图). Penalties range from rejection and reduced traffic to deposit deductions. The response already carries duplication signals (`is_duplicated_in_shop`, `copy_from_spu_id`), so the platform does detect copies.
- **Copyright and brands.** Product photos and copy belong to the original seller. Brand complaints can take products down and put the shop at risk.
- **Mitigation built into M2a:**
  - Everything is imported as a **draft**. Nothing goes live without the user's review.
  - Titles are rewritten before upload.
  - Images can be replaced by **supplier images the user is entitled to use**, such as the linked 1688 offer (1688 dropship sellers generally allow resellers to use their images).
  - Brand-word blocklist.
- **Collection.** Collection stays outside this system. Automated collection of WeChat pages can break WeChat's terms of service and put the collecting account at risk. Longer term, M6 (1688 Open Platform) is the more sustainable source.

### M3 Shipping templates
- CRUD backed by the freight-template APIs. Rules cover pricing by piece or weight, regional first/additional fees, free-shipping conditions, and non-delivery regions (uses a province/city picker).
- **Assignment:**
  - Single product: a dropdown in the editor.
  - **Bulk:** filter products → select → "Apply template X". This becomes a job that calls `product/update` per product, rate-limited, with a result report.
  - Default template per category (rule: "new products in category C get template T").
- Local copy of templates, re-synced on demand so the UI is fast.

### M4 Orders and fulfillment
- **Sync:** the push event triggers a fetch of order detail. An incremental poll (`list/get` by update time, every 5 min) acts as a safety net. A nightly reconciliation job covers the last 7 days.
- **Order board:** tabs by status (to pay / **to ship** / shipped / completed / after-sale). Filters cover time, product, SKU, buyer remark, and timeout risk.
- **Fulfillment flow:**
  1. Select orders → generate a **picking list** (aggregated by SKU) and packing slips (PDF/Excel).
  2. Enter tracking numbers by scanner, one at a time, or by **Excel import** (order ID, courier, tracking no.).
  3. Bulk ship job → `delivery/send` per order (supports split/partial shipment) → per-order result.
- **Alerts:** unshipped near the ship-by deadline, after-sale requests pending, address changes.
- **Optional (phase 3):** e-waybill (电子面单) via a courier aggregator API, then auto-fill the tracking number.

### M5 Logistics
- Courier dictionary from `deliverycompanylist/get`, cached. Map local courier names to WeChat codes for Excel import.
- Each shipment record holds order ID, package, courier, tracking no., ship time, and status.
- Tracking view: use what the WeChat order detail exposes, or optionally a tracking aggregator (e.g. Kuaidi100) for richer events.

### M7 Category applications (类目申请)

A shop can only list products in categories it has opened, and opening each category means an application. Many categories need a **deposit (保证金)**, and some need **qualification documents (资质)**. This module lets a user open many categories in a single pass instead of one at a time in the WeChat back office.

**Tabs:** Apply · Application records · My categories.

**Category sync**
- Background job: walk the whole tree with the category APIs, which go down to level 4. For each node, store the parent, level, deposit amount, whether qualifications are needed and which ones, and whether the node is a leaf.
- The global tree is the same for every shop, so it is cached once for the whole system and refreshed daily. Each shop also gets its own overlay: opened / pending audit / rejected (reason) / available.
- A "Sync categories" button runs the sync by hand and shows progress ("同步中").

**Apply screen** (UX like the reference screenshot)
- Four cascading columns (levels 1–4). Each row shows a checkbox, the name, the number of child categories, and the deposit range under it (e.g. `1000–5000元`). Each column has a "select all" checkbox.
- Ticking a parent selects all of its eligible children. A half-checked state shows when only some of them are selected.
- Search box: matches names at any level and highlights the path.
- **Deposit threshold filter:** unlimited / ≤1000 / ≤2000 / ≤3000 / ≤4000 / ≤5000 / ≤10000 / ≤20000 元.
- Toggles (on by default): **hide already-opened**, **hide categories that need qualifications**, **hide categories with an application in progress**.
- Footer: "Selected N categories", the **estimated deposit** for the selection, and two buttons: **Apply now** and **Apply for several stores**.

**One-click "apply for everything"**
- One button: "Select all eligible". It respects the current filters (for example, all categories with deposit ≤ X that need no qualifications and are not yet opened or pending) and shows a confirmation with the count and the deposit impact.
- Categories that need qualifications are **not** skipped silently. They go into a "needs documents" list. The user uploads the licence or certificate once, and it is stored in TOS and reused for every category that needs the same type. They can then be submitted in a second pass.
- **Multi-store apply:** pick several connected shops. The selection is checked against each shop's own overlay, so categories a shop already has are dropped for that shop.

**Execution**
- Each selection becomes a job: one API call per leaf category (or per batch, if the API accepts lists). Calls go through the per-tenant rate limiter, retry with backoff on throttling, and are idempotent: a category already opened or pending is skipped.
- Per-category results (submitted / failed with WeChat error message / skipped) are shown live and can be exported to Excel.
- Audit results come from push events where available, plus a status poll every 30 minutes for pending applications. Approved categories show up under "My categories" automatically and in the M2 category picker.

**Rules to confirm against the official docs before building**
- The exact endpoint names, and whether the apply API takes one category or a list.
- How the deposit is charged: per category, or the shop's highest requirement. The deposit estimate in the footer follows that rule.
- Whether WeChat limits how many applications a shop can have pending at once or submit per day. If so, the job queues the rest and continues automatically.
- The app **never** pays deposits or bypasses review. It only submits applications. Paying deposits stays in the WeChat back office.

### M6 1688 bulk sourcing
- **Inputs:**
  - A pasted keyword list (one per line)
  - An uploaded Excel file of keywords and target prices
  - **Multiple images** for image search
  - Selected store products ("find a cheaper source for these")
- **Batch engine:** each query becomes a job that respects 1688 quota (token bucket per tenant + global). Results are cached for 24h keyed by query hash.
- **Result grid:** offer image, title, price ladder (阶梯价), MOQ, supplier name/rating/years, location, 48h ship rate, repurchase rate, and dropship (一件代发) support.
- **Filters and scoring:** price range, MOQ ≤ N, dropship only, supplier rating ≥ X. A weighted "recommend score" is configurable.
- **Actions:**
  - Favorite or compare offers.
  - **Link an offer to a store product** (enables cost/margin view and stock alerts later).
  - **Create a draft product from the offer:** pull title, images, and SKU specs → editor (M2), with a pricing rule such as cost × 1.8 + 5 rounded to .9. The user must review before submitting, and image rights/branding are the user's responsibility (shown as a reminder).
- **Exports:** Excel of the selected results.

---

## 5. Data model (core tables)

```
tenant(id, name, plan, created_at)
user(id, tenant_id, phone/email, password_hash, role)
wx_store_account(id, tenant_id, appid, appsecret_enc, callback_token, aes_key_enc, ip_whitelisted_at, status)
product(id, tenant_id, wx_product_id, title, category_ids, status, freight_template_id, draft_json, wx_snapshot_json, updated_at)
sku(id, product_id, wx_sku_id, sku_code, spec_json, price, stock)
media(id, tenant_id, src_url, tos_key, wx_url, hash)
freight_template(id, tenant_id, wx_template_id, name, rule_json, synced_at)
order(id, tenant_id, wx_order_id, status, pay_time, ship_deadline, buyer_remark, address_enc, amount, raw_json, updated_at)
order_item(id, order_id, wx_product_id, wx_sku_id, qty, price)
shipment(id, tenant_id, order_id, delivery_company_id, waybill_no, items_json, shipped_at, result)
aftersale(id, tenant_id, wx_aftersale_id, order_id, type, status, raw_json)
wx_category(id, parent_id, level, name, is_leaf, deposit, need_qualification, qualification_json, synced_at)   -- global
shop_category(tenant_id, category_id, status[opened|pending|rejected|available], reason, updated_at)
category_application(id, tenant_id, job_id, category_id, wx_audit_id, status, error_msg, submitted_at, decided_at)
qualification_file(id, tenant_id, type, tos_key, wx_media_id, expires_at)
source_query(id, tenant_id, batch_id, type[keyword|image|product], input, status)
source_offer(id, query_id, offer_id, title, price_json, moq, supplier_json, raw_json)
product_source_link(product_id, offer_id, cost_price, note)
job(id, tenant_id, type, total, done, failed, status, report_tos_key, created_by)
audit_log(id, tenant_id, user_id, action, target, detail_json, at)
```

Buyer addresses and phone numbers are personal information under China's PIPL. Encrypt them at rest, mask them in the UI, set a retention limit, and restrict access to the warehouse role plus anyone with explicit permission.

---

## 6. Non-functional requirements
- **Rate limiting:** a per-tenant token bucket for WeChat and 1688 calls. Handle `45009` (quota exceeded) with backoff, and show it in the UI.
- **Idempotency:** client-generated request IDs for bulk jobs. Shipping checks the order's current status first, so the same order is never shipped twice.
- **Retries:** exponential backoff for network/5xx errors. Business errors are not retried; they are reported per row.
- **Performance target:** 1k orders/day per tenant and 100 tenants on a single 4C8G ECS + RDS. Scale workers horizontally.
- **Backups:** RDS automatic backups (7 days) plus a TOS lifecycle policy.

---

## 7. Deployment on your Volcano Engine server (phase 1)

| Resource | Suggested spec |
|---|---|
| ECS | 4 vCPU / 8 GB, Ubuntu 22.04, mainland region (e.g. 华北2-北京 / 华东2-上海) |
| EIP | 1 fixed IP (add it to every tenant's WeChat whitelist) |
| RDS MySQL | 2C4G, or run MySQL in Docker on the ECS at first to save cost |
| Redis | Volcano Engine Redis 1 GB, or Docker |
| TOS | 1 bucket, private, signed URLs |
| Domain + SSL | ICP-filed domain; free/paid certificate on CLB or Nginx |

`docker-compose.yml` services: `nginx`, `web`, `api`, `worker`, `scheduler`, `mysql`*, `redis`* (*optional if using managed services). CI: a GitHub Actions build pushes images to Volcano Engine **CR** (container registry); deploy by SSH + `docker compose pull && up -d`.

---

## 8. Roadmap

| Phase | Duration (1–2 devs) | Deliverables |
|---|---|---|
| **0. Prep** | week 0 (in parallel) | Apply for 1688 Open Platform app + search APIs; ICP filing; Volcano Engine resources; test WeChat Store. |
| **1. MVP** | weeks 1–5 | Auth & tenants, M1 store connection, M3 freight templates (CRUD + bulk assign), M4 order sync + manual/Excel bulk ship, M5 courier dictionary. |
| **2. Products & categories** | weeks 6–10 | M7 category sync + one-click/multi-store apply (needed before products can be listed), M2 product editor, **M2a JSON import**, image pipeline, bulk Excel import, audit tracking. |
| **3. Sourcing** | weeks 11–14 | M6 batch keyword/image search, compare, link offers, draft-from-offer. |
| **4. Extras** | later | Third-party-platform (QR authorization) mode, e-waybill printing, margin dashboard, 1688 auto-purchase/dropship, billing/plans. |

Phases 1–2 are not blocked by 1688 approval. M6 can switch from a licensed provider to the official API later through the gateway interface.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 1688 search API not approved, or quota too small | Apply early. Keep the gateway abstraction so a licensed provider can be swapped in. Cache results aggressively. |
| WeChat API changes or new required fields | Isolate everything in the WeChat Gateway. Store `raw_json`. Add contract tests against a test store. |
| Users mis-configure the IP whitelist or secret | Connection wizard with live checks and clear error-code explanations. |
| Product rejections (category qualifications, image rules) | Validate required attributes before submission. Show audit reasons inline. |
| Listings imported from collected JSON (M2a) are flagged as copies, rejected, or draw complaints | Draft-only import, title rewrite, image replacement with supplier images, brand blocklist, dedupe. Collection stays outside the system. |
| Copyrighted images or brands copied from 1688 | Draft-only flow, a user confirmation step, and a brand-word warning list. |
| Mass category applications rejected or throttled by WeChat | Respect any rate limits or pending caps. Skip ineligible categories before submitting. Show the WeChat rejection reasons. Qualification categories go through a guided second pass. |
| Personal-data compliance | Encryption, masking, role restrictions, retention policy. |

---

## 10. Open questions for you
1. Connection mode: is **self-managed AppID/AppSecret** (option A) fine for v1, or do you have a company entity and want to go straight to a **third-party platform** (option B)?
2. Who are the users: only your own stores, or external merchants you will charge (affects billing and isolation depth)?
3. Expected scale: number of stores and orders per day?
4. Do you already have 1688 Open Platform access? Which solution or API package?
5. Team language preference: Python (FastAPI) or TypeScript (NestJS)?
6. Category applications: should "apply for all" by default include only categories that need **no** qualifications, and is there a maximum deposit you want as the default filter?
7. Do you need e-waybill printing in v1, or is manual/Excel tracking-number entry enough?
8. Collected JSON: will all future files be in format A (the `data.data` string)? The 5 page-state dumps (format E) are unsupported for now. Should title rewriting use a model, and if so, which one (Volcano Engine Doubao is the natural fit)?

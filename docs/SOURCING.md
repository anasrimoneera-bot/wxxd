# 找货源 (1688 Sourcing): Design

Status: v1.2 · 2026-11-11.
- **S1 is implemented:** image search, goods price only, shipping excluded.
- **S2 is on hold:** the owner cannot apply for the 代发解决方案（分销买家版） yet, so the checkout-preview cost waits.
- v1.1 changed the landed-cost method to 1688's checkout preview for a specific SKU and address, per owner feedback.

## 1. Goal

From product main images (up to **10 per batch**), find supply sources on 1688 and show them in a
**large-image grid**, as in the reference tool 阿里牛顿 (`aibuy.1688.com` → 找品 → 批量图搜). For each
image, compute the **optimal total landed cost (到手成本)**: product price **plus shipping**, so the user
can pick the cheapest reliable source and link it to the product.

Entry points:
- **商品云库** or **商品管理**: select products (max 10) → **找货源**. Their main images are used.
- **找货源 page:** paste up to 10 image URLs (one per line), as in the reference tool's 图片链接搜.

## 2. Where the data comes from

The page the owner referenced,
`https://aibuy.1688.com/landingpage/new-home/find-products.html?...`, is a **web front end** for 1688's
AI 找品 service. It has **no public API**. Driving it automatically (headless browser, copying its private
calls) would mean:
- captchas and login walls;
- breaking whenever the page changes;
- breaching 1688's terms.

`iframe` embedding is also normally blocked. So the system **does not scrape that page**. Instead:

| Layer | Source | Use |
|---|---|---|
| **Primary** | **1688 official AI gateway**: `POST https://gateway.1688.com/api/alibaba.1688.find.product/1.0.0`, signed with an AK from **clawhub.1688.com** (HMAC-SHA256 headers `x-csk-*`). This is the protocol used by 1688's own published skill `next-1688/1688-product-find`. | Image search with `imageUrl` or `imgBase64`, `pageSize`, `sortType`, `purchaseAmount`. Returns offer id, title, `currentPrice`, `quantityBegin` (MOQ), `soldOut`, `company`, `imageUrl`, `detailUrl`. These are the same fields the reference page shows. |
| **Detail (SKUs)** | Official 1688 product detail (`alibaba.product.get`): every SKU (`specId`) with its attributes, price, stock and 代发 price | Lets the user (or auto-match) pick the exact 1688 SKU that corresponds to each store SKU |
| **Exact cost (checkout preview)** | **1688 open platform `com.alibaba.trade:alibaba.createOrder.preview`** (创建订单前预览), called with `offerId + specId + quantity + delivery address` | Returns what the checkout page shows: tier price for that quantity, **freight to that address**, platform discounts, total. No order is created. This is the authoritative cost. |
| **Estimate (fallback)** | Freight template from the detail, or the 包邮 tag | Used only when the preview is unavailable; always labelled **估算** |
| **Fallback adapters** (optional, switchable in 系统设置) | everyinfra `alibaba1688.image_search` (~¥0.04/call), Nexscope, OneBound `item_search_img` / `item_get` (`post_fee`) | Used only if the official route is unavailable or over quota. |
| **Manual** | Button **在 1688 打开**: opens the referenced aibuy page | Lets the user continue by hand (询盘, 同款找商) in 1688's own UI. |

All providers sit behind one interface, `SourcingProvider.search(image_url, qty) → [Candidate]` and
`.detail(offer_id) → Detail`, so a provider can be swapped without touching the screens.

## 3. Landed cost (到手成本): from 1688's checkout preview

The owner confirmed on the 1688 site that the real cost is only known at checkout. In the 微压锅 example:
- the same offer costs ¥26.80 or ¥29.80 depending on the SKU (color/size);
- shipping was ¥0 to 广东深圳 only because the offer is 包邮. Other offers and addresses differ.

So the landed cost is **not estimated from the search result**. It is computed for a specific SKU, quantity and address, exactly as checkout does it.

**Step 1: SKU matching (store SKU ↔ 1688 SKU).** After a source is chosen, each SKU of the store product (e.g. 颜色:灰色 8L) is matched to a 1688 SKU (`specId`, e.g. "8L钛不粘胖墩墩【灰色】"):
- auto-suggested by attribute text similarity (color, capacity, size words) plus the SKU thumbnail image similarity;
- the user confirms or changes each pair in a two-column dialog;
- unmatched store SKUs are flagged.

**Step 2: checkout preview per SKU.**
```
preview = alibaba.createOrder.preview(
    addressParam = default delivery address  (系统设置: your warehouse, or a typical customer address for 代发),
    cargoParamList = [{offerId, specId, quantity: q}],   q = 1 for 代发 (dropship), or your batch size
    flow = "general" | "fenxiao" (代发 price))
landed_unit(sku) = preview.total_payable / q        # goods (tier price) + freight − platform discounts
freight(sku)     = preview.freight                  # 0 when 包邮 to that address
```
- Results are cached per (offer, SKU, q, province) for 24 h, since prices and freight change.
- When the preview is unavailable (no authorization, API error), the template estimate is shown, labelled **估算**, and is never auto-selected.

**Step 3: margin per store SKU.**
`毛利 = store SKU retail price (商品管理, live) − landed_unit − WeChat service fee (rate in 系统设置) − 运费险 (if on)`.
The product row shows the lowest and highest SKU margin. A negative margin is flagged red.

**最优 (best) source per image:**
1. Search and similarity narrow the candidates (§4).
2. The cheapest candidates (default top 5) get a quick preview, using the SKU closest to the store product.
3. The candidate with the lowest *previewed* landed cost, passing the filters below, gets the 最优 badge:
   - MOQ ≤ q;
   - similarity ≥ threshold;
   - optional: supplier years, 48h shipping, repurchase rate.

   Ties go to sales, then supplier rating.

The full per-SKU matching (step 1) is done when the user clicks 设为货源.

## 4. Matching quality

1688 returns *similar* offers, not guaranteed identical ones. Each candidate image is compared with the
source image:
- **pHash distance:** cheap, catches the same photo or re-crops;
- **colour-histogram / ORB check:** different photo of the same product.

The result is a **相似度 0–100%**. Candidates below the threshold are greyed out and never chosen as 最优.
This runs on the server with Pillow, already installed. No extra service is needed.

## 5. User interface

**A. Input bar**, as in the reference tool:
- tabs **图片链接搜** (textarea, one URL per line, max 10) and **从商品选择** (picker from 商品云库 or 商品管理, max 10);
- purchase quantity `q`, destination province;
- **开始找货** button.

**B. Results, large-image mode (default):**
- One row per source image. On the left, the source image with the product title and current retail price. On the right, a horizontal strip of up to 20 candidate cards (240 × 240 image).
- Each card shows:
  - **到手价 ¥X.XX** (large), split as 货价 + 运费;
  - MOQ, 销量, 回头率, 诚信通 years, 发货 time, 发货地;
  - **相似度**;
  - a green **最优** badge on the best one.
- Sort: 综合 / 到手价 / 销量 / 相似度. Filters: MOQ, ships within 48h, similarity, price range. Toggle **大图模式 / 列表模式**, as in the reference.
- Card actions:
  - **设为货源**: links the offer to the product and saves cost price and supplier;
  - **在 1688 打开**: detail page;
  - **询盘**: 1688 inquiry link.
- Row action: **自动选最优**, for all rows at once.

**C. After linking:**
- 商品管理 and 商品云库 show **成本价 / 毛利 / 货源** columns.
- A daily recheck of linked offers flags "货源已下架 / 涨价 / 断货". That feeds directly into the new bulk-delete flow: select unavailable products → delete, and also delete or delist them in the WeChat store.

## 6. Processing

1. **Create:** `POST /api/sourcing/jobs {images[≤10] | product_ids[≤10], quantity, province}` creates a job with up to 10 queries.
2. **Search:** the existing workers pick up queries; one provider call per image, then a detail call for the top N (default 8) candidates to get price tiers and freight.
3. **Cache:** search results by image hash, 7 days; offer details by offer id, 24 hours. Repeated searches are free and instant.
4. **Progress:** the UI polls `GET /api/sourcing/jobs/{id}` and renders rows as each image completes (10 images ≈ 10–30 s).
5. **Image URLs:** WeChat CDN images are passed as `imageUrl`. If the provider cannot fetch them, the worker downloads the image and sends `imgBase64` instead, as with the WeChat upload fix.

## 7. Data model

```
sourcing_job(id, tenant_id, created_by, quantity, province, status, created_at)
sourcing_query(id, job_id, image_url, image_hash, library_product_id?, store_product_id?, status, error)
sourcing_candidate(id, query_id, provider, offer_id, title, image_url, detail_url, supplier, price, price_tiers JSON,
                   moq, sold, repurchase_rate, ship_from, ship_hours, freight, freight_known, landed_unit,
                   similarity, rank, is_best)
product_source_link(id, tenant_id, library_product_id?, store_product_id?, offer_id, provider, supplier, detail_url,
                    linked_at, last_checked_at, availability)
source_sku_map(link_id, store_sku_id, offer_spec_id, spec_name, quantity, unit_price, freight, landed_unit,
               preview_at, estimated BOOL)                       -- one row per store SKU

sourcing_cache(key, payload JSON, expires_at)
```

## 8. Settings and cost control

- **系统设置 → 货源接口:**
  - provider (官方 1688 / everyinfra / Nexscope);
  - keys (encrypted, masked);
  - default province;
  - WeChat service-fee rate;
  - similarity threshold.
- **Per-plan monthly quota** of image searches (用户管理), counted per tenant.
- **Cost:** with the official AK, per its terms (to be confirmed). With everyinfra, ~¥0.04 per image search plus detail calls. The cache keeps repeated searches free.

## 9. Phases

| Phase | Scope |
|---|---|
| **S1** ✅ built | Official gateway image search (signature verified against 1688's reference implementation); 10-image batch from pasted links or selected 商品云库 / 商品管理 products; large-image grid and list mode; **goods price only (不含运费)**, MOQ, sales, similarity, supplier, tags; 最优 = cheapest offer among close matches whose MOQ fits the quantity; 设为货源 → 货源价 column in 商品云库 / 商品管理; 1688 打开; 7-day cache per image. AK in 系统设置 with a 测试连接 button. |
| **S2** ⏸ on hold (owner cannot apply for the 分销买家 solution yet) | SKU matching (store SKU ↔ 1688 `specId`) + **checkout preview** (`alibaba.createOrder.preview`) → exact landed cost and margin per SKU, 最优 badge, 自动选最优 |
| **S2+** (optional) | The same 1688 authorization also allows 代发下单 (`alibaba.trade.fenxiaoOrder.create`): place the purchase order when a WeChat order arrives |
| **S3** | Daily availability/price recheck of linked sources → alerts → bulk delete / delist (the deletion flow already exists) |

## 10. Needed from the owner before S1

1. An **AK from clawhub.1688.com**, and its terms: price, quota, commercial or SaaS use. Enter it in 系统设置; never paste it in chat.
2. **For S2, the exact cost:** a 1688 open-platform app (open.1688.com) with the 交易 / 分销 solution that includes `alibaba.createOrder.preview`, and **authorization of your 1688 buyer account** (OAuth). This is the same account and address you check out with. It is separate from the clawhub AK. Company verification and approval may be required, so start early.
3. The **default delivery address** used for the preview (your warehouse, or a typical customer address for 代发), and the typical purchase quantity (1 = dropship).
4. The WeChat **service-fee rate** to use in the margin.

## 11. Risks

- The official gateway's terms and quota are unknown until the AK is issued. The fallback adapters cover that gap.
- Without the 1688 buyer authorization only estimates are possible. These are clearly labelled 估算 and never auto-selected as 最优.
- Prices and freight change; previews are cached at most 24 h, and a preview is not a guarantee of the final checkout price.
- Similar ≠ identical. The similarity score and manual confirmation before 设为货源 mitigate this.

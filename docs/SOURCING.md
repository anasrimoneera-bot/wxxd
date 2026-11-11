# 找货源 (1688 Sourcing): Design

Status: design v1 · 2026-11-11 · not implemented yet

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
| **Detail and freight** | Official 1688 product detail (`alibaba.product.get`: price tiers, `shippingInfo` / freight template, unit weight, ship-from), or the gateway's detail/compare capability if the AK includes it. *To be confirmed with the AK.* | Price for the purchase quantity, and shipping fee. |
| **Fallback adapters** (optional, switchable in 系统设置) | everyinfra `alibaba1688.image_search` (~¥0.04/call), Nexscope, OneBound `item_search_img` / `item_get` (`post_fee`) | Used only if the official route is unavailable or over quota. |
| **Manual** | Button **在 1688 打开**: opens the referenced aibuy page | Lets the user continue by hand (询盘, 同款找商) in 1688's own UI. |

All providers sit behind one interface, `SourcingProvider.search(image_url, qty) → [Candidate]` and
`.detail(offer_id) → Detail`, so a provider can be swapped without touching the screens.

## 3. Landed cost (到手成本)

For a candidate offer, purchase quantity `q` (default **1** for dropshipping, set per batch) and
destination province `P` (default from 系统设置: your warehouse, or a "typical customer" province):

```
unit_price(q)   = price tier from detail.priceRanges where q ≥ startQuantity   (fallback: currentPrice)
freight(q, P)   = 0                                   if the offer is 包邮 to P
                = first_fee + ceil((weight(q) − first_unit) / add_unit) × add_fee   by the freight template for P
                  (by weight or by piece, as the template defines)
                = unknown                             if no freight data → shown as "运费待确认", ranked last
landed_unit     = unit_price(q) + freight(q, P) / q
```

Shown next to it:
- **毛利 (gross margin)** = store retail price − landed_unit − WeChat service fee (rate in 系统设置) − 运费险 (if enabled).
  The retail price comes from 商品管理 (live store price), or from the 上货模板 price formula for products not yet published.
- **最优 (best) source per image** = the lowest `landed_unit` among candidates that pass all filters:
  - MOQ ≤ q (otherwise "起订量 N");
  - image similarity ≥ threshold (default 0.80, see §4);
  - optional: supplier years ≥ N, ships within 48h, repurchase rate ≥ X%.

Ties are broken by sales, then supplier rating.

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
product_source_link(id, tenant_id, library_product_id?, store_product_id?, offer_id, provider, cost_price, freight,
                    landed_unit, supplier, detail_url, linked_at, last_checked_at, availability)
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
| **S1** | Official gateway image search; 10-image batch; large-image grid; price / MOQ / sales / similarity; 设为货源; 在 1688 打开. Landed cost uses 包邮 info only. |
| **S2** | Detail + freight → full landed cost and margin, 最优 badge, 自动选最优 |
| **S3** | Daily availability/price recheck of linked sources → alerts → bulk delete / delist (the deletion flow already exists) |

## 10. Needed from the owner before S1

1. An **AK from clawhub.1688.com**, and its terms: price, quota, commercial or SaaS use. Enter it in 系统设置; never paste it in chat.
2. A **default destination province** for the freight calculation, and the typical purchase quantity (1 = dropship).
3. The WeChat **service-fee rate** to use in the margin.

## 11. Risks

- The official gateway's terms and quota are unknown until the AK is issued. The fallback adapters cover that gap.
- Freight data may be missing for some offers. These are shown as "运费待确认" and never auto-selected as 最优.
- Similar ≠ identical. The similarity score and manual confirmation before 设为货源 mitigate this.

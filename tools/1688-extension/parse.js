/* 1688 货源助手 — reading 1688 pages (pure functions, also tested with node: see test/parse.test.js).
 * 1688 changes its pages now and then, so every value has several fallbacks. */
(function (root) {
  'use strict'

  function offerIdFromUrl(url) {
    const m = String(url || '').match(/offer\/(\d{6,})/) || String(url || '').match(/[?&](?:offerId|offer_id)=(\d{6,})/)
    return m ? m[1] : ''
  }

  function toNum(v) {
    if (v === null || v === undefined || v === '') return null
    const n = parseFloat(String(v).replace(/[^\d.]/g, ''))
    return isFinite(n) ? n : null
  }

  // Cut the JSON object that follows `"key":` out of a script, by matching braces.
  function jsonAfter(text, key) {
    const i = text.indexOf('"' + key + '"')
    if (i < 0) return null
    let j = text.indexOf('{', i + key.length + 2)
    if (j < 0) return null
    let depth = 0
    let inStr = false
    for (let k = j; k < text.length; k++) {
      const c = text[k]
      if (inStr) {
        if (c === '\\') k++
        else if (c === '"') inStr = false
      } else if (c === '"') inStr = true
      else if (c === '{') depth++
      else if (c === '}') {
        depth--
        if (depth === 0) {
          try { return JSON.parse(text.slice(j, k + 1)) } catch (e) { return null }
        }
      }
    }
    return null
  }

  function stringAfter(text, key) {
    const m = text.match(new RegExp('"' + key + '"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"'))
    if (!m) return ''
    try { return JSON.parse('"' + m[1] + '"') } catch (e) { return m[1] }
  }

  function unescapeHtml(s) {
    return String(s || '').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&').replace(/&quot;/g, '"')
  }

  /* Detail page data embedded in the page's scripts (skuModel.skuInfoMap):
   * {"杏色加长款 建议身高158以上&gt;S 75-90": {"skuId": 1, "price": "39.90", "canBookCount": 708}, ...} */
  function skusFromScripts(text, fallbackPrice) {
    const map = jsonAfter(text, 'skuInfoMap')
    if (!map || typeof map !== 'object') return []
    const out = []
    for (const name of Object.keys(map)) {
      const v = map[name] || {}
      const price = toNum(v.discountPrice) ?? toNum(v.price) ?? toNum(v.promotionPrice) ?? fallbackPrice
      out.push({
        name: unescapeHtml(name).split('>').map((x) => x.trim()).join(' / '),
        sku_id: String(v.skuId || v.specId || name),
        price: price,
        stock: v.canBookCount ?? v.saleCount ?? null,
      })
    }
    return out
  }

  function freightFromText(text) {
    const m = String(text || '').match(/运费\s*[:：]?\s*[¥￥]\s*([\d.]+)/) || String(text || '').match(/运费\s*([\d.]+)\s*元/)
    if (m) return parseFloat(m[1])
    return /包邮|免运费/.test(String(text || '')) ? 0 : null
  }

  function priceFromText(text) {
    const m = String(text || '').match(/[¥￥]\s*([\d]+(?:\.\d+)?)/)
    return m ? parseFloat(m[1]) : null
  }

  // "已售40+件" / "销量:80+" / "全网销量200+件" / "已售1.2万" -> number
  function salesFromText(text) {
    const m = String(text || '').match(/(?:已售|销量|成交)\s*[:：]?\s*([\d.]+)\s*(万)?/)
    if (!m) return 0
    return Math.round(parseFloat(m[1]) * (m[2] ? 10000 : 1))
  }

  function norm(s) {
    return String(s || '').toLowerCase().replace(/[\s\-_/:：;；,，()（）【】\[\]>]+/g, '')
  }

  /* Our specs (labels like "杏色加长款 / S 75-90") -> the 1688 SKU that contains every part of the label.
   * Returns {ourKey: skuIndex | -1}. Single SKU on both sides: matched directly. */
  function matchSpecs(ours, skus) {
    const out = {}
    if (!skus.length) return out
    if (skus.length === 1) { ours.forEach((o) => { out[o.key] = 0 }); return out }
    const used = new Set()
    ours.forEach((o, idx) => {
      const parts = String(o.label || '').split('/').map(norm).filter(Boolean)
      let best = -1
      let bestScore = 0
      skus.forEach((s, i) => {
        const n = norm(s.name)
        const score = parts.reduce((acc, p) => acc + (n.includes(p) ? p.length : 0), 0)
        if (score > bestScore || (score === bestScore && score > 0 && used.has(best) && !used.has(i))) {
          best = i
          bestScore = score
        }
      })
      if (best < 0 && ours.length === skus.length) best = idx  // nothing in common: same position
      if (best >= 0) used.add(best)
      out[o.key] = best
    })
    return out
  }

  /* 最优 among checked candidates: 同款 with the most sales; otherwise the cheapest 相似. */
  function pickBest(cands) {
    const same = cands.filter((c) => c.verdict === '同款')
    if (same.length) return same.slice().sort((a, b) => (b.sales || 0) - (a.sales || 0) || (a.price || 1e9) - (b.price || 1e9))[0]
    const similar = cands.filter((c) => c.verdict === '相似' && c.price)
    return similar.length ? similar.slice().sort((a, b) => a.price - b.price)[0] : null
  }

  /* 万 / + aware count: '10万+' -> 100000, 4500 -> 4500 */
  function countNum(v) {
    if (v == null || v === '') return null
    if (typeof v === 'number') return v
    const m = String(v).match(/([\d.]+)\s*(万)?/)
    return m ? Math.round(parseFloat(m[1]) * (m[2] ? 10000 : 1)) : null
  }

  function priceNum(v) {
    if (v == null) return null
    if (typeof v === 'object') return priceNum(v.price ?? v.value ?? v.salePrice ?? v.minPrice ?? v.priceStr)
    return toNum(v)
  }

  /* Offers in a search response (JSON from the page's own requests): any object with a 1688 offer id
   * and an image. Field names differ between 1688 pages, so several are tried. */
  function offersFromJson(root) {
    const out = []
    const seen = new Set()
    const walk = (o, d) => {
      if (!o || typeof o !== 'object' || d > 14) return
      if (Array.isArray(o)) { for (const x of o) walk(x, d + 1); return }
      const link = o.detailUrl || o.offerUrl || o.linkUrl || o.url
      let id = o.offerId ?? o.offer_id ?? o.offerID ?? (typeof link === 'string' ? offerIdFromUrl(link) : null)
      let img = o.imageUrl || o.imgUrl || o.offerPicUrl || o.picUrl || o.mainImage || o.offerImg || o.imageURL ||
        (typeof o.image === 'string' ? o.image : o.image && (o.image.imgUrl || o.image.url))
      if (id != null && /^\d{6,}$/.test(String(id)) && typeof img === 'string' && img) {
        id = String(id)
        if (!seen.has(id)) {
          seen.add(id)
          if (img.startsWith('//')) img = 'https:' + img
          const title = String(o.subject || o.title || o.offerTitle || o.name || '').replace(/<[^>]+>/g, '')
          out.push({ key: id, image_url: img, title: unescapeHtml(title).slice(0, 120),
            price: priceNum(o.price ?? o.priceInfo ?? o.offerPrice ?? o.salePrice ?? o.minPrice),
            sales: countNum(o.saleQuantity ?? o.soldOut ?? o.sales ?? o.saleCount ?? o.monthSold ?? o.booked ?? o.sold),
            url: `https://detail.1688.com/offer/${id}.html` })
        }
      }
      for (const k in o) if (o[k] && typeof o[k] === 'object') walk(o[k], d + 1)
    }
    walk(root, 0)
    return out
  }

  const api = { offersFromJson, countNum, offerIdFromUrl, toNum, jsonAfter, stringAfter, skusFromScripts, freightFromText, priceFromText,
    salesFromText, matchSpecs, pickBest, unescapeHtml }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.HY1688 = api
})(typeof self !== 'undefined' ? self : this)

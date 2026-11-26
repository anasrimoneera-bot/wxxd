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

  /* the 运费 on an offer page: 「运费 ¥3起」「运费：3.00元」「快递 ¥2.5」 (the ¥ may be drawn with CSS, so
   * it is optional); 0 only for the offer's own 包邮 (「快递 包邮」「运费 包邮/免运费」 or a lone 「包邮」 label),
   * not for 「满99包邮」 or 「退货包运费」 */
  function freightFromText(text) {
    const t = String(text || '')
    const m = t.match(/运费(?!险)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*(?:元)?/) ||
      t.match(/(?:快递|物流)\s*[:：]?\s*[¥￥]\s*(\d+(?:\.\d+)?)/) || t.match(/(?:快递|运费)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*元/)
    if (m) return parseFloat(m[1])
    if (/(?:快递|运费|物流)\s*[:：]?\s*(?:包邮|免运费|免费)/.test(t) || /(?:^|\n)\s*(?:包邮|免运费)\s*(?:\n|$)/.test(t)) return 0
    return null
  }
  /* the 运费 in the page's own data (a fallback when the text has none) */
  function freightFromScripts(scripts) {
    const m = String(scripts || '').match(/"(?:freightFee|postFee|deliveryFee|freightCost|firstUnitFee|logisticsFee|totalCost)"\s*:\s*"?(\d+(?:\.\d+)?)"?/)
    return m ? parseFloat(m[1]) : null
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
  function grams(s) {  // character pairs of a name: '水系油壶' -> 水系 系油 油壶
    const t = norm(s)
    const g = new Set()
    for (let i = 0; i < t.length - 1; i++) g.add(t.slice(i, i + 2))
    if (t.length === 1) g.add(t)
    return g
  }
  function similarity(a, b) {  // 0..1: shared character pairs (Dice)
    const A = grams(a), B = grams(b)
    if (!A.size || !B.size) return 0
    let n = 0
    for (const g of A) if (B.has(g)) n++
    return (2 * n) / (A.size + B.size)
  }

  /* {our spec key: 1688 SKU index or -1}. Each of our specs gets the 1688 SKU whose name is closest:
   * every part of ours found in the 1688 name counts most (颜色 / 尺码 words), then how much of the two
   * names is the same (so 「细条纹油壶1000ml香槟色」 picks 「水系油壶 1000ml 香槟色」, not the 连帽 one).
   * A 1688 SKU can serve several of our specs; on a tie an unused one is preferred. Nothing in common:
   * the same position when both have as many specs, else -1 (you choose). */
  function matchSpecs(ours, skus) {
    const out = {}
    if (!skus.length) return out
    if (skus.length === 1) { ours.forEach((o) => { out[o.key] = 0 }); return out }
    const used = new Set()
    ours.forEach((o, idx) => {
      const label = String(o.label || '')
      const parts = label.split('/').map(norm).filter(Boolean)
      let best = -1
      let bestScore = 0
      skus.forEach((s, i) => {
        const n = norm(s.name)
        const contained = parts.reduce((acc, p) => acc + (n.includes(p) ? p.length : 0), 0)
        const score = contained * 10 + similarity(label, s.name) * 10
        if (score > bestScore + 1e-9 || (Math.abs(score - bestScore) < 1e-9 && score > 0 && used.has(best) && !used.has(i))) {
          best = i
          bestScore = score
        }
      })
      if (bestScore < 1.5) best = -1  // hardly anything in common: not a match
      if (best < 0 && ours.length === skus.length) best = idx  // same position
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
      let id = o.offerId ?? o.offer_id ?? o.offerID ?? (typeof link === 'string' ? offerIdFromUrl(link) : null) ??
        o.itemId ?? o.item_id ?? o.productId ?? o.product_id ??
        ((o.subject || o.title) && (o.price != null || o.priceInfo) ? o.id : null)  // a plain id: only on something offer-like
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

  /* 1688 上架: what the detail page says about the offer, from its scripts */
  function arrayAfter(text, key) {  // the JSON array after "key": (like jsonAfter for objects)
    const i = text.indexOf('"' + key + '"')
    if (i < 0) return null
    const j = text.indexOf('[', i + key.length + 2)
    if (j < 0 || /[{"\w]/.test(text.slice(i + key.length + 2, j).replace(/[\s:]/g, ''))) return null
    let depth = 0, inStr = false
    for (let k = j; k < text.length; k++) {
      const c = text[k]
      if (inStr) { if (c === '\\') k++; else if (c === '"') inStr = false }
      else if (c === '"') inStr = true
      else if (c === '[') depth++
      else if (c === ']') { depth--; if (depth === 0) { try { return JSON.parse(text.slice(j, k + 1)) } catch (e) { return null } } }
    }
    return null
  }
  /* a picture link at full size: drop the 1688 thumbnail suffix (….jpg_220x220.jpg / ….220x220.jpg) */
  function fullImage(u) {
    if (!u || typeof u !== 'string') return ''
    let x = u.trim().replace(/^\/\//, 'https://')
    x = x.replace(/(\.(?:jpe?g|png|webp))(?:_\d+x\d+[^/]*|\.\d+x\d+\.(?:jpe?g|png|webp)|_\.webp|_sum\.jpg)$/i, '$1')
    return /^https?:\/\//.test(x) ? x : ''
  }
  /* the offer's main pictures (gallery), in order */
  function offerImages(text) {
    const out = []
    const add = (u) => { const f = fullImage(u); if (f && /alicdn|1688|cbu0/.test(f) && !out.includes(f)) out.push(f) }
    for (const key of ['offerImgList', 'imageList', 'images', 'mainImageList', 'imgList']) {
      const arr = arrayAfter(text, key)
      if (!Array.isArray(arr)) continue
      for (const x of arr) add(typeof x === 'string' ? x : x && (x.fullPathImageURI || x.originalImageURI || x.imageURI || x.url || x.imgUrl))
      if (out.length) break
    }
    if (!out.length) add(stringAfter(text, 'mainImage'))
    return out.slice(0, 15)
  }
  /* 销售属性: [{prop: '颜色', values: [{name, image}]}] */
  function skuProps(text) {
    const arr = arrayAfter(text, 'skuProps') || arrayAfter(text, 'skuPropList') || []
    return (Array.isArray(arr) ? arr : []).map((p) => ({
      prop: unescapeHtml(String(p.prop || p.name || p.propName || '规格')).trim(),
      values: (p.value || p.values || []).map((v) => ({ name: unescapeHtml(String(v.name || v.value || '')).trim(),
        image: fullImage(v.imageUrl || v.image || v.imgUrl || '') })),
    })).filter((p) => p.values.length)
  }
  /* skus with their specs as {name, value} (from skuProps) and the spec picture */
  function offerSkus(text, fallbackPrice) {
    const props = skuProps(text)
    return skusFromScripts(text, fallbackPrice).map((k) => {
      const vals = k.name.split(' / ')
      const attrs = vals.map((v, i) => ({ name: (props[i] && props[i].prop) || (vals.length > 1 ? `规格${i + 1}` : '规格'), value: v }))
      let image = ''
      vals.forEach((v, i) => { const hit = props[i] && props[i].values.find((x) => x.name === v); if (!image && hit && hit.image) image = hit.image })
      return { ...k, attrs, image }
    })
  }
  /* the link of the offer's 详情 (description) document, if the page loads it separately */
  const descUrl = (text) => stringAfter(text, 'detailUrl') || stringAfter(text, 'descUrl') || stringAfter(text, 'offerDetailUrl') || ''
  /* the pictures in a description (HTML, or a JS file with the HTML in a string) */
  function imagesFromHtml(html) {
    const out = []
    const re = /(?:src|data-src|data-lazyload-src|data-lazy-src)\s*=\s*\\?["']([^"'\\]+)/gi
    let m
    while ((m = re.exec(String(html || '')))) {
      const f = fullImage(m[1])
      if (f && /\.(jpe?g|png|webp)/i.test(f) && !/spaceball|\.gif|1x1/i.test(f) && !out.includes(f)) out.push(f)
    }
    return out.slice(0, 50)
  }

  /* the offer's main video (mp4): "videoUrl", wirelessVideo.videoUrls, or any video link in the scripts */
  function offerVideo(text) {
    const t = String(text || '').replace(/\\u002F/gi, '/').replace(/\\\//g, '/')
    const ok = (u) => /^(https?:)?\/\/[^\s"']+\.(mp4|mov)(\?|$)/i.test(u || '')
    for (const key of ['videoUrl', 'video_url', 'mainVideoUrl', 'android', 'ios']) {
      const u = stringAfter(t, key)
      if (ok(u)) return u.replace(/^\/\//, 'https://')
    }
    const m = t.match(/(https?:)?\/\/[^\s"'<>\\]+?\.(?:mp4|mov)(?:\?[^\s"'<>\\]*)?/i)
    return m ? m[0].replace(/^\/\//, 'https://') : ''
  }

  const api = { offersFromJson, offerVideo, offerImages, skuProps, offerSkus, descUrl, imagesFromHtml, fullImage, arrayAfter, countNum, offerIdFromUrl, toNum, jsonAfter, stringAfter, skusFromScripts, freightFromText, freightFromScripts, priceFromText,
    salesFromText, matchSpecs, similarity, pickBest, unescapeHtml }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.HY1688 = api
})(typeof self !== 'undefined' ? self : this)

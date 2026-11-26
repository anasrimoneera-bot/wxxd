/* 1688 货源助手 — runs in the 1688 page itself (MAIN world), before the page's scripts: it reads the
 * page's own search responses (fetch / XMLHttpRequest) and passes the offers in them (id, image, title,
 * price, sales) to content.js. Nothing is changed or sent anywhere else. */
(function () {
  const P = window.HY1688
  if (!P || window.__hy1688Hook) return
  window.__hy1688Hook = true
  const post = (text) => {
    if (!text || text.length < 50 || text.length > 8e6) return
    window.__hyNet = (window.__hyNet || 0) + 1
    let data = null
    try { data = JSON.parse(text) } catch (e) {
      const m = text.match(/^[\w$.]+\(([\s\S]*)\)\s*;?\s*$/)  // JSONP: callback({...})
      if (m) { try { data = JSON.parse(m[1]) } catch (e2) { return } } else return
    }
    handle(data)
  }
  const handle = (data) => {
    const offers = P.offersFromJson(data)
    if (offers.length) window.postMessage({ hy1688: 'offers', offers }, location.origin)
  }
  // JSONP (mtop): <script src="...callback=mtopjsonp12"> calls window.mtopjsonp12(data)
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.tagName !== 'SCRIPT' || !n.src) continue
      const cb = (n.src.match(/[?&](?:callback|jsonp|cb)=([\w$.]+)/) || [])[1]
      if (!cb || cb.includes('.')) continue
      const wrap = (fn) => function (data) { try { window.__hyNet = (window.__hyNet || 0) + 1; handle(data) } catch (e) { /* ignore */ } return fn.apply(this, arguments) }
      if (typeof window[cb] === 'function' && !window[cb].__hy) { const w = wrap(window[cb]); w.__hy = true; window[cb] = w }
    }
  }).observe(document, { childList: true, subtree: true })
  const origFetch = window.fetch
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args)
    p.then((res) => { try { res.clone().text().then(post).catch(() => {}) } catch (e) { /* opaque */ } }).catch(() => {})
    return p
  }
  const open = XMLHttpRequest.prototype.open
  XMLHttpRequest.prototype.open = function (...args) {
    this.addEventListener('load', function () {
      try { if (!this.responseType || this.responseType === 'text') post(this.responseText) } catch (e) { /* ignore */ }
    })
    return open.apply(this, args)
  }
})()

/* 批量找货源: content.js hands over the product images (data: URLs); the next time the page opens its
 * file picker (input type=file), the images are given to it instead of showing the picker — the same
 * as choosing those files by hand. */
;(function () {
  if (window.__hy1688Files) return
  window.__hy1688Files = true
  let pending = null
  const toFile = (f) => {
    const [head, b64] = f.dataUrl.split(',')
    const bin = atob(b64)
    const buf = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i)
    return new File([buf], f.name, { type: (head.match(/data:([^;]+)/) || [])[1] || 'image/jpeg' })
  }
  function give(input) {
    const dt = new DataTransfer()
    for (const f of (input.multiple ? pending : pending.slice(0, 1))) dt.items.add(toFile(f))
    input.files = dt.files
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    window.postMessage({ hy1688: 'filesUsed', n: dt.files.length, multiple: !!input.multiple }, location.origin)
    pending = null
  }
  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data) return
    if (e.data.hy1688 === 'files') { pending = e.data.files; window.postMessage({ hy1688: 'filesReady' }, location.origin) }
    if (e.data.hy1688 === 'filesCancel') pending = null
  })
  // a <label for=…> or a person-like click on the input itself opens the picker without input.click():
  // catch that click (cancelling it keeps the picker closed) and give the files instead
  document.addEventListener('click', (e) => {
    const t = e.target
    if (pending && t instanceof HTMLInputElement && t.type === 'file') { e.preventDefault(); give(t) }
  }, true)
  const click = HTMLInputElement.prototype.click
  HTMLInputElement.prototype.click = function () {
    if (this.type === 'file' && pending) return give(this)
    return click.apply(this, arguments)
  }
  const showPicker = HTMLInputElement.prototype.showPicker
  if (showPicker) HTMLInputElement.prototype.showPicker = function () {
    if (this.type === 'file' && pending) return give(this)
    return showPicker.apply(this, arguments)
  }
})()

/* Result cards: when the page's search responses can't be read, find each card's offer from the page
 * itself (links, data-* attributes, or the React / Vue props behind the card) and mark the card with
 * data-hy-offer / data-hy-title / data-hy-price / data-hy-sales, which content.js reads. */
;(function () {
  if (window.__hy1688Scan) return
  window.__hy1688Scan = true
  const P = window.HY1688
  const SKIP = new Set(['children', 'return', 'child', 'sibling', 'stateNode', '_owner', 'parent', 'root', 'appContext', 'vnode', 'subTree', 'ctx', 'provides'])
  function offerIn(o, d, seen) {
    if (!o || typeof o !== 'object' || d > 5 || seen.has(o)) return null
    seen.add(o)
    const id = o.offerId ?? o.offer_id ?? o.offerID
    if (id != null && /^\d{6,}$/.test(String(id))) return o
    for (const k of Object.keys(o).slice(0, 60)) {
      if (SKIP.has(k) || k.startsWith('_') || k.startsWith('$')) continue
      let v
      try { v = o[k] } catch (e) { continue }
      if (v && typeof v === 'object' && !(v instanceof Node)) { const r = offerIn(v, d + 1, seen); if (r) return r }
    }
    return null
  }
  function propsOf(el) {
    const out = []
    for (const k of Object.keys(el)) {
      if (k.startsWith('__reactProps$')) out.push(el[k])
      if (k.startsWith('__reactFiber$')) { let f = el[k]; for (let i = 0; i < 6 && f; i++, f = f.return) if (f.memoizedProps) out.push(f.memoizedProps) }
    }
    let c = el.__vueParentComponent
    for (let i = 0; i < 4 && c; i++, c = c.parent) out.push(c.props, c.attrs, c.setupState)
    if (el.__vue__) { let v = el.__vue__; for (let i = 0; i < 4 && v; i++, v = v.$parent) out.push(v.$props, v.$data) }
    return out
  }
  function scan() {
    for (const el of document.querySelectorAll('[data-hy-offer]')) el.removeAttribute('data-hy-offer')
    const imgs = [...document.images].filter((im) => im.offsetParent !== null && im.getBoundingClientRect().width >= 100)
    let n = 0
    for (const img of imgs) {
      let el = img, found = null, card = null
      for (let i = 0; i < 8 && el && el !== document.body && !found; i++, el = el.parentElement) {
        const a = el.tagName === 'A' ? el : null
        const id = a && P.offerIdFromUrl(a.href)
        if (id) { found = { offerId: id }; card = el; break }
        for (const at of el.attributes || []) {
          if (/offer|data-id|data-item/i.test(at.name) && /^\d{6,}$/.test(at.value)) { found = { offerId: at.value }; break }
        }
        if (!found) for (const p of propsOf(el)) { const o = offerIn(p, 0, new Set()); if (o) { found = o; break } }
        card = el
      }
      if (!found) continue
      // the card: up to the element that shows a price
      let c = card
      for (let i = 0; i < 6 && c && !/[¥￥]\s*\d/.test(c.innerText || ''); i++) c = c.parentElement
      c = c || card
      const id = String(found.offerId ?? found.offer_id ?? found.offerID)
      c.setAttribute('data-hy-offer', id)
      c.setAttribute('data-hy-img', img.currentSrc || img.src)
      const title = String(found.subject || found.title || found.offerTitle || img.alt || '').replace(/<[^>]+>/g, '')
      if (title) c.setAttribute('data-hy-title', title.slice(0, 120))
      const price = found.price ?? found.priceInfo ?? found.offerPrice
      const pn = price && typeof price === 'object' ? (price.price ?? price.value) : price
      if (pn != null) c.setAttribute('data-hy-price', String(pn))
      const sales = found.saleQuantity ?? found.soldOut ?? found.sales ?? found.saleCount ?? found.booked
      if (sales != null) c.setAttribute('data-hy-sales', String(sales))
      n++
    }
    return n
  }
  /* probe: what offer does a card open? Click it with opening blocked and catch the link (window.open
   * or a link click); nothing is opened. */
  function probe(el) {
    let url = null
    const open = window.open
    window.open = function (u) { url = String(u || ''); return null }
    const stop = (e) => {
      const a = e.target && e.target.closest && e.target.closest('a[href]')
      if (a) { url = url || a.href; e.preventDefault() }
    }
    document.addEventListener('click', stop, true)
    try { el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })) } catch (e) { /* ignore */ }
    document.removeEventListener('click', stop, true)
    window.open = open
    return url
  }
  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data) return
    if (e.data.hy1688 === 'scan') window.postMessage({ hy1688: 'scanned', n: scan(), net: window.__hyNet || 0 }, location.origin)
    if (e.data.hy1688 === 'probe') {
      const out = {}
      for (const el of document.querySelectorAll('[data-hy-probe]')) {
        const id = P.offerIdFromUrl(probe(el) || '')
        if (id) { out[el.getAttribute('data-hy-probe')] = id; el.setAttribute('data-hy-offer', id) }
        el.removeAttribute('data-hy-probe')
      }
      window.postMessage({ hy1688: 'probed', ids: out }, location.origin)
    }
  })
})()

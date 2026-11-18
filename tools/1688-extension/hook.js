/* 1688 货源助手 — runs in the 1688 page itself (MAIN world), before the page's scripts: it reads the
 * page's own search responses (fetch / XMLHttpRequest) and passes the offers in them (id, image, title,
 * price, sales) to content.js. Nothing is changed or sent anywhere else. */
(function () {
  const P = window.HY1688
  if (!P || window.__hy1688Hook) return
  window.__hy1688Hook = true
  const post = (text) => {
    if (!text || text.length < 50 || text.length > 8e6 || text.indexOf('ffer') < 0) return
    let data = null
    try { data = JSON.parse(text) } catch (e) {
      const m = text.match(/^[\w$.]+\(([\s\S]*)\)\s*;?\s*$/)  // JSONP: callback({...})
      if (m) { try { data = JSON.parse(m[1]) } catch (e2) { return } } else return
    }
    const offers = P.offersFromJson(data)
    if (offers.length) window.postMessage({ hy1688: 'offers', offers }, location.origin)
  }
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

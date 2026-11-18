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

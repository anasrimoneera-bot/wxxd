/* 1688 货源助手 — runs on 1688 pages in the user's own (logged-in) browser.
 *  - search / image-search result pages: a 「选为货源」 button on every result card; you choose the 货源.
 *  - offer detail pages: read the title, supplier, main image, every SKU price and the 运费, map the
 *    1688 SKUs to our specs, and 「设为货源」 saves it (written to 商品云库 and every store).
 * Nothing is clicked or submitted on 1688 itself: the page is only read. */
(function () {
  const P = window.HY1688
  const send = (msg) => new Promise((resolve, reject) =>
    chrome.runtime.sendMessage(msg, (r) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message))
      r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错'))
    }))
  const call = (method, path, body) => send({ type: 'api', method, path, body })
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
  const abs = (u) => { try { return new URL(u, location.href).href } catch (e) { return '' } }
  const bigImg = (u) => abs(String(u || '').replace(/_\d+x\d+(q\d+)?\.(jpg|png|webp).*$/i, '').replace(/\.(\d+x\d+)\./, '.'))

  let current = null
  const netOffers = new Map()  // offers read from the page's own search responses (hook.js)
  let groupSeq = 0  // search responses seen (a response with offers = the results of one search)
  let lastGroup = []
  window.addEventListener('message', (e) => {
    if (e.source !== window || !e.data || e.data.hy1688 !== 'offers') return
    for (const o of e.data.offers) if (!netOffers.has(o.key)) netOffers.set(o.key, o)
    if (e.data.offers.length >= 3) { lastGroup = e.data.offers; groupSeq++ }
  })
  const imgKey = (u) => (String(u || '').split('?')[0].split('/').pop() || '').replace(/(\.(jpe?g|png|webp)).*$/i, '$1').replace(/_\d+x\d+.*$/, '')
  let tabItem = null  // the product of this tab in 批量找货源 (null: the one picked in the popup)
  const setStatus = (data) => (tabItem || current ? send({ type: 'setStatus', key: (tabItem || current).key || `${(tabItem || current).kind}:${(tabItem || current).id}`, data }).catch(() => {}) : Promise.resolve())
  let panel = null
  let quiet = false  // our own storage write: don't re-render

  function makePanel() {
    if (panel) return panel
    panel = document.createElement('div')
    panel.id = 'hy-panel'
    panel.innerHTML = '<header><span>1688 货源助手</span><span class="hy-toggle">—</span></header><div class="hy-body"></div>'
    panel.querySelector('header').onclick = () => panel.classList.toggle('min')
    // our product's main image: click to show it large next to the panel (to compare with the 1688
    // pictures), click again to close
    panel.addEventListener('click', (e) => {
      const img = e.target.closest && e.target.closest('img.hy-zoomable')
      if (!img) return
      e.stopPropagation()
      toggleZoom(img.src)
    })
    document.body.appendChild(panel)
    return panel
  }
  const body = () => makePanel().querySelector('.hy-body')

  function toggleZoom(src) {
    let z = document.getElementById('hy-zoom')
    if (z && z.dataset.src === src) { z.remove(); return }  // the same picture again: close
    if (!z) {
      z = document.createElement('div')
      z.id = 'hy-zoom'
      z.title = '点击取消放大'
      z.onclick = () => z.remove()
      document.body.appendChild(z)
    }
    z.dataset.src = src
    z.innerHTML = `<img src="${esc(src)}"><div>我们的商品主图 · 点击取消放大</div>`
  }

  function curHtml() {
    if (!current) return '<p class="hy-muted">还没有选择商品：点浏览器右上角的插件图标，选一个要找货源的商品。</p>'
    return `<div class="hy-cur"><img class="hy-zoomable" title="点击放大" src="${esc(current.image)}"><div style="flex:1;min-width:0">
      <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(current.title)}">${esc(current.title)}</div>
      <div class="hy-muted">${tabItem ? '<b style="color:#ff6a00">批量</b> · ' : ''}${esc(current.store)} · ${current.specs.length} 个规格 · 售价 ¥${yuan(current.price_min)}</div></div></div>`
  }

  /* ---------- search result pages ---------- */
  function cardOf(img) {  // the result card around an image: up to the element that shows a price
    let el = img
    for (let i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement
      if (/[¥￥]\s*\d/.test(el.innerText || '')) return el
    }
    return null
  }

  function collectCards(only) {
    if (only || netOffers.size) {  // from the page's own data: works whatever the page layout
      const imgs = [...document.images]
      return (only || [...netOffers.values()]).map((o) => {
        const k = imgKey(o.image_url)
        const img = k && imgs.find((i) => imgKey(i.currentSrc || i.src) === k)
        return { ...o, el: img ? cardOf(img) : null }
      })
    }
    const seen = new Map()
    for (const a of document.querySelectorAll('a[href*="offer/"], a[href*="offerId="], a[href*="offer_id="]')) {
      const id = P.offerIdFromUrl(a.href)
      if (!id || seen.has(id)) continue
      // the card: the closest ancestor that holds an image and a price, but only one offer
      let el = a
      for (let i = 0; i < 8 && el.parentElement; i++) {
        const up = el.parentElement
        const ids = new Set([...up.querySelectorAll('a[href]')].map((x) => P.offerIdFromUrl(x.href)).filter(Boolean))
        if (ids.size > 1) break
        el = up
        if (el.querySelector('img') && /[¥￥]\s*\d/.test(el.innerText || '')) break
      }
      const img = el.querySelector('img')
      if (!img) continue
      const text = el.innerText || ''
      const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || ''
      if (!src || src.startsWith('data:')) continue
      seen.set(id, { key: id, el, image_url: bigImg(src), title: (img.alt || a.title || a.innerText || text.split('\n')[0] || '').trim().slice(0, 120),
        price: P.priceFromText(text), sales: P.salesFromText(text), url: a.href })
    }
    return [...seen.values()]
  }

  function badge(card, cls, text, tip) {
    const el = card.el
    if (!el) return
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative'
    let b = el.querySelector(':scope > .hy-badge')
    if (!b) { b = document.createElement('span'); b.className = 'hy-badge'; el.appendChild(b) }
    b.className = 'hy-badge ' + cls
    b.textContent = text
    b.title = tip || ''
  }

  /* a 「选为货源」 button on each result card; onPick(card) when one is clicked */
  function pickButtons(cards, onPick, chosen) {
    document.querySelectorAll('.hy-pick').forEach((x) => x.remove())
    document.querySelectorAll('.hy-best').forEach((x) => x.classList.remove('hy-best'))
    let n = 0
    for (const c of cards) {
      if (!c.el) continue
      if (getComputedStyle(c.el).position === 'static') c.el.style.position = 'relative'
      const b = document.createElement('button')
      b.className = 'hy-pick' + (chosen === c.key ? ' on' : '')
      b.textContent = chosen === c.key ? '已选为货源' : '选为货源'
      if (chosen === c.key) c.el.classList.add('hy-best')
      const stop = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation() }
      for (const t of ['pointerdown', 'mousedown', 'mouseup', 'pointerup']) b.addEventListener(t, stop, true)
      b.addEventListener('click', (e) => { stop(e); onPick(c) }, true)
      c.el.appendChild(b)
      n++
    }
    return n
  }

  function renderSearch() {
    body().innerHTML = curHtml() + (current ? `
      <button id="hy-show-pick">在本页商品上显示「选为货源」</button>
      <p class="hy-muted">找到合适的货源后点它卡片上的「选为货源」，会打开它的详情页核对规格价格。</p>
      <div id="hy-msg"></div>` : '')
    const b = panel.querySelector('#hy-show-pick'); if (b) b.onclick = showPickHere
  }

  async function showPickHere() {
    const msg = panel.querySelector('#hy-msg')
    let cards = collectCards().filter((c) => c.el)
    if (!cards.length) cards = await scanCards()
    const n = pickButtons(cards, (c) => send({ type: 'openDetail', key: current.key, url: `https://detail.1688.com/offer/${c.key}.html` }))
    if (msg) msg.innerHTML = n ? `<span class="hy-msg">已在 ${n} 个商品上显示「选为货源」。</span>` : '<span class="hy-err">这个页面上没找到商品卡片（可以先往下滚动，让图片加载出来）</span>'
  }

  /* ---------- offer detail pages ---------- */
  function readOffer() {
    const scripts = [...document.scripts].map((s) => s.textContent).join('\n')
    const text = document.body.innerText || ''
    const offer_id = P.offerIdFromUrl(location.href)
    const meta = (p) => (document.querySelector(`meta[property="${p}"]`) || {}).content || ''
    const title = (meta('og:title') || P.stringAfter(scripts, 'subject') || (document.querySelector('h1') || {}).innerText || document.title).trim()
    let image = meta('og:image') || P.stringAfter(scripts, 'mainImage') || ''
    if (!image) {
      const im = [...document.images].find((i) => i.naturalWidth >= 300 && /alicdn|cbu01/.test(i.src))
      image = im ? im.src : ''
    }
    const supplier = P.stringAfter(scripts, 'companyName') || P.stringAfter(scripts, 'sellerLoginId') || ''
    const price = P.priceFromText(text)
    const skus = P.skusFromScripts(scripts, price)
    const moqM = text.match(/(\d+)\s*(?:件|个|套|双|条)\s*起批/)
    return { offer_id, title: P.unescapeHtml(title).slice(0, 250), image_url: bigImg(image), supplier: P.unescapeHtml(supplier),
      price: price || (skus.length ? Math.min(...skus.map((s) => s.price || Infinity)) : null), skus,
      freight: P.freightFromText(text), moq: moqM ? Number(moqM[1]) : null, detail_url: location.href.split('#')[0] }
  }

  async function renderDetail() {
    const box = body()
    if (!current) { box.innerHTML = curHtml(); return }
    const o = readOffer()
    if (!o.offer_id) { box.innerHTML = curHtml() + '<p class="hy-err">读不到商品编号</p>'; return }
    const map = o.skus.length ? P.matchSpecs(current.specs, o.skus) : {}
    const memKey = `${current.kind}:${current.id}:${o.offer_id}`  // your own choices for this pair are remembered
    const { specMaps = {} } = await chrome.storage.local.get('specMaps')
    const saved = specMaps[memKey] || {}
    for (const k in saved) if (saved[k] < o.skus.length) map[k] = saved[k]
    const minPrice = o.skus.length ? Math.min(...o.skus.map((x) => x.price ?? Infinity)) : o.price
    const opts = (sel) => o.skus.map((s, i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${esc(s.name)} ¥${s.price ?? '-'}</option>`).join('')
    const rows = current.specs.map((sp) => {
      const i = map[sp.key] ?? -1
      const p = i >= 0 ? o.skus[i].price : (isFinite(minPrice) ? minPrice : o.price)
      return `<tr data-key="${esc(sp.key)}" ${i < 0 && o.skus.length ? 'style="background:#fff7e6"' : ''}><td style="width:34%">${esc(sp.label)}</td>
        <td>${o.skus.length ? `<select class="hy-sku"><option value="-1">（不对应）</option>${opts(i)}</select>` : ''}</td>
        <td style="width:70px"><input class="hy-price" type="number" step="0.01" min="0" value="${p ?? ''}"></td></tr>`
    }).join('')
    box.innerHTML = curHtml() + `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <img src="${esc(o.image_url)}" style="width:48px;height:48px;object-fit:cover;border-radius:4px">
        <div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.title)}</div>
        <div class="hy-muted">${esc(o.supplier)} · ${o.skus.length} 个 1688 规格</div></div></div>
      ${o.skus.length > 1 ? `<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin:4px 0">
        <span class="hy-muted">已自动对应，</span><span id="hy-unmatched" class="hy-muted"></span>
        <button id="hy-order" style="padding:2px 8px;background:#909399">按顺序对应</button>
        <select id="hy-all" style="width:auto;max-width:150px"><option value="">全部用…</option>${opts(-2)}</select></div>` : ''}
      <table><tr><th>我们的规格</th><th>1688 规格</th><th>货源价</th></tr>${rows}</table>
      <div style="display:flex;gap:6px;align-items:center"><span class="hy-muted" style="white-space:nowrap">运费 ¥</span>
        <input id="hy-freight" type="number" step="0.01" min="0" value="${o.freight ?? ''}" placeholder="没读到就手动填" style="width:90px">
        <span class="hy-muted">采购成本 = 货源价 + 运费</span></div>
      <div style="display:flex;gap:6px;margin-top:10px"><button class="hy-ok" id="hy-save">设为货源</button></div>
      <div id="hy-msg" style="margin-top:6px"></div>
      ${current.source && current.source.offer_id ? `<p class="hy-muted">当前货源：${esc(current.source.offer_id)}（¥${yuan(current.source.price)}），设为货源会替换它。</p>` : ''}`
    const setRow = (tr, idx) => {
      const sel = tr.querySelector('.hy-sku'); if (!sel) return
      sel.value = String(idx)
      const sku = o.skus[idx]
      if (sku && sku.price != null) tr.querySelector('.hy-price').value = sku.price
      tr.style.background = idx < 0 ? '#fff7e6' : ''
    }
    const remember = async () => {
      const m = {}
      box.querySelectorAll('tr[data-key]').forEach((tr) => { const sel = tr.querySelector('.hy-sku'); if (sel) m[tr.dataset.key] = Number(sel.value) })
      const st = await chrome.storage.local.get('specMaps')
      const all = st.specMaps || {}
      all[memKey] = m
      const keys = Object.keys(all); if (keys.length > 300) delete all[keys[0]]
      await chrome.storage.local.set({ specMaps: all })
    }
    const countUnmatched = () => {
      const n = [...box.querySelectorAll('.hy-sku')].filter((x) => x.value === '-1').length
      const el = box.querySelector('#hy-unmatched')
      if (el) el.innerHTML = n ? `<b style="color:#e6a23c">${n} 个规格没对应上（橙色），请选一下</b>` : '全部对应上了，核对价格即可。'
    }
    box.querySelectorAll('.hy-sku').forEach((s) => (s.onchange = () => { setRow(s.closest('tr'), Number(s.value)); countUnmatched(); remember() }))
    const order = box.querySelector('#hy-order')
    if (order) order.onclick = () => { box.querySelectorAll('tr[data-key]').forEach((tr, i) => setRow(tr, i < o.skus.length ? i : -1)); countUnmatched(); remember() }
    const all = box.querySelector('#hy-all')
    if (all) all.onchange = () => { if (all.value !== '') { box.querySelectorAll('tr[data-key]').forEach((tr) => setRow(tr, Number(all.value))); all.value = ''; countUnmatched(); remember() } }
    countUnmatched()
    box.querySelector('#hy-save').onclick = async () => {
      const msg = box.querySelector('#hy-msg'); const btn = box.querySelector('#hy-save')
      const sku_prices = {}
      for (const tr of box.querySelectorAll('tr[data-key]')) {
        const v = parseFloat(tr.querySelector('.hy-price').value)
        if (!(v > 0)) { msg.innerHTML = `<span class="hy-err">「${esc(tr.cells[0].innerText)}」的货源价要大于 0</span>`; return }
        sku_prices[tr.dataset.key] = v
      }
      const fr = box.querySelector('#hy-freight').value
      const vals = Object.values(sku_prices)
      const price = vals.length ? Math.min(...vals) : o.price
      if (!(price > 0)) { msg.innerHTML = '<span class="hy-err">读不到价格，请手动填写货源价</span>'; return }
      btn.disabled = true
      try {
        const d = await call('POST', '/api/ext/link', { kind: current.kind, id: current.id,
          offer: { offer_id: o.offer_id, title: o.title, image_url: o.image_url, detail_url: o.detail_url, supplier: o.supplier, price, moq: o.moq },
          sku_prices, freight: fr === '' ? null : Number(fr) })
        current.source = d.source
        if (!tabItem) { quiet = true; await chrome.storage.local.set({ current }) }
        msg.innerHTML = '<span class="hy-msg">已设为货源，商品云库和各店铺里的同一商品已同步。</span>' +
          '<br><span class="hy-muted">1 秒后自动关闭本页…</span>'
        await setStatus({ status: 'done', note: `货源 ¥${price}`, best: { offer_id: o.offer_id, price, title: o.title } })
        setTimeout(() => send({ type: 'closeMe' }).catch(() => {}), 1200)  // done: close this page
      } catch (e) { msg.innerHTML = `<span class="hy-err">${esc(e.message)}</span>` } finally { btn.disabled = false }
    }
  }

  /* ---------- 批量: search 1688 with the main image ---------- */
  const visible = (e) => !!e && (e.checkVisibility ? e.checkVisibility() : e.getClientRects().length > 0)
  function byText(texts, root) {
    const els = [...(root || document).querySelectorAll('button, a, span, div, li, label')]
      .filter((e) => visible(e) && texts.includes((e.textContent || '').replace(/\s+/g, '')))
    return els.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0] || null
  }
  function dialog() {
    const ds = [...document.querySelectorAll('[role=dialog], [class*=modal], [class*=Modal], [class*=dialog], [class*=Dialog], [class*=popup]')]
      .filter((d) => visible(d) && d.querySelector('textarea, input'))
    return ds.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0] || null
  }
  function okButton(field) {  // 确认 of the dialog the field is in
    let el = field
    for (let i = 0; i < 10 && el; i++, el = el.parentElement) {
      const b = byText(['确认', '确定', '搜索', '搜同款'], el)
      if (b && b !== field) return b
    }
    return byText(['确认', '确定'])
  }
  function typeInto(field, text) {  // works with the page's framework (React / Vue) listening for input
    const proto = field.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(field, text)
    field.dispatchEvent(new Event('input', { bubbles: true }))
    field.dispatchEvent(new Event('change', { bubbles: true }))
  }

  /* 找品 page: 图片链接搜 -> paste our main-image link -> 确认 */
  async function linkSearch(url) {
    let btn = null
    for (let i = 0; i < 20 && !(btn = byText(['图片链接搜'])); i++) await sleep(500)
    if (!btn) return false
    btn.click()
    let dlg = null
    for (let i = 0; i < 15 && !(dlg = dialog()); i++) await sleep(300)
    if (!dlg) return false
    const tab = byText(['图片链接搜'], dlg)  // the dialog may open on another tab
    if (tab) { tab.click(); await sleep(300) }
    const field = dlg.querySelector('textarea') || dlg.querySelector('input[type=text], input:not([type])')
    if (!field) return false
    field.focus()
    typeInto(field, url)
    await sleep(400)
    const ok = okButton(field)
    if (!ok) return false
    netOffers.clear()  // from here on: the results of this search
    ok.click()
    return true
  }

  async function waitResults() {
    for (let i = 0; i < 25; i++) {
      await sleep(1000)
      if (netOffers.size >= 3) { await sleep(1500); return true }
    }
    return collectCards().length > 0
  }

  function fileInput() {
    const all = [...document.querySelectorAll('input[type=file]')]
    return all.find((i) => /image/.test(i.accept || '')) || all[0] || null
  }

  async function uploadImage() {
    const box = body()
    box.innerHTML = curHtml() + '<p class="hy-muted">正在上传主图搜同款…</p><div id="hy-msg"></div>'
    await setStatus({ status: 'searching', note: '' })
    const { server } = await chrome.storage.local.get('server')
    if (current.image_proxy && server && (/air\.1688\.com/.test(location.host) || byText(['图片链接搜']))) {
      const url = server.replace(/\/+$/, '') + current.image_proxy
      if (await linkSearch(url)) {
        await setStatus({ status: 'uploaded' })
        if (await waitResults()) return autoVerify()
      }
    }
    let input = null
    for (let i = 0; i < 20 && !(input = fileInput()); i++) await sleep(500)  // the page may still be loading
    let dataUrl = null
    try { dataUrl = await send({ type: 'fetchImage', item: current }) } catch (e) {
      box.querySelector('#hy-msg').innerHTML = `<span class="hy-err">${esc(e.message)}</span>`
      return setStatus({ status: 'error', note: e.message })
    }
    if (!input) return manualUpload(dataUrl, '没找到 1688 的图片上传入口')
    const blob = await (await fetch(dataUrl)).blob()
    const dt = new DataTransfer()
    dt.items.add(new File([blob], `${current.kind}-${current.id}.jpg`, { type: blob.type || 'image/jpeg' }))
    input.files = dt.files
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await setStatus({ status: 'uploaded' })
    const from = location.href
    for (let i = 0; i < 20; i++) {  // the results replace this page, or load into it
      await sleep(1000)
      if (location.href !== from) { await sleep(1500); break }  // a new page ends this script; an in-page change goes on
      if (/youyuan|imageSearch|pailitao|image_search/i.test(location.href) || collectCards().length >= 8) break
    }
    if (location.href === from && !/youyuan|imageSearch|pailitao|image_search/i.test(location.href))
      return manualUpload(dataUrl, '上传后没有看到搜索结果')
    await autoVerify()
  }

  function manualUpload(dataUrl, why) {
    const box = body()
    box.innerHTML = curHtml() + `<p class="hy-err">${esc(why)}。</p>
      <p class="hy-muted">请点 1688 的「图片链接搜」，粘贴主图链接（点下面的按钮复制）后确认；或者把下面的主图拖进图片搜索框。搜索结果出来后点「显示选为货源」。</p>
      <button id="hy-copy-link" style="margin-bottom:6px">复制主图链接</button><br>
      <img src="${dataUrl}" style="width:120px;height:120px;object-fit:cover;border:1px solid #eee;border-radius:4px" draggable="true">
      <div style="margin-top:6px"><button id="hy-verify-manual">显示选为货源</button></div><div id="hy-msg"></div>`
    box.querySelector('#hy-verify-manual').onclick = () => { renderSearch(); showPickHere() }
    box.querySelector('#hy-copy-link').onclick = async (e) => {
      const { server } = await chrome.storage.local.get('server')
      await navigator.clipboard.writeText(server.replace(/\/+$/, '') + current.image_proxy).catch(() => {})
      e.target.textContent = '已复制'
    }
    setStatus({ status: 'searching', note: why })
  }

  async function autoVerify() {
    renderSearch()
    for (let i = 0; i < 15 && collectCards().length < 8; i++) {  // let the result cards and images load
      window.scrollBy(0, 600); await sleep(800)
    }
    window.scrollTo(0, 0)
    await sleep(500)
    if (panel.querySelector('#hy-show-pick')) await showPickHere()
  }

  /* ---------- 批量找货源 on one 找品 page ----------
   * 批量图搜 with the products' main images as files (1688 cannot download them from a link); 大图模式; then each
   * uploaded picture in turn: click it, wait for its results, you choose the 货源 (「选为货源」) or 跳过. */
  let pageBatch = null
  let running = false
  const STATUS = { waiting: '等待', searching: '打开中', selecting: '请选择货源', confirm: '已选，待设货源', done: '已设货源', nomatch: '已跳过', error: '出错' }
  const sendStatus = (key, data) => send({ type: 'setStatus', key, data }).catch(() => {})


  function renderBatch(msg, active) {
    const box = body()
    const items = pageBatch.items
    const done = items.filter((x) => x.status === 'done').length
    box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <b>批量找货源 · ${items.length} 个商品</b><span class="hy-muted">已设货源 ${done}/${items.length}</span></div>
      <div id="hy-msg" class="hy-muted" style="margin-bottom:6px">${msg || ''}</div>
      ${items.map((it, i) => `<div style="display:flex;gap:6px;align-items:center;padding:4px 0;border-bottom:1px solid #f0f0f0">
        <img class="hy-zoomable" title="点击放大，再点一次取消" src="${esc(it.image)}" style="width:40px;height:40px;object-fit:cover;border-radius:4px">
        <div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(it.title)}">${i + 1}. ${esc(it.title)}</div>
          <div class="hy-muted" ${i === active ? 'style="color:#ff6a00;font-weight:600"' : ''}>${STATUS[it.status] || it.status}${it.note ? ' · ' + esc(it.note) : ''}</div></div>
        ${it.status === 'selecting' && choose ? '<button id="hy-skip" style="background:#909399">跳过</button>' : ''}
        ${it.best && it.status !== 'selecting' ? `<button data-open="${i}" ${it.status === 'done' ? 'class="hy-ok"' : ''}>${it.status === 'done' ? '已设' : '打开货源'}</button>` : ''}
        ${!running && it.status !== 'done' && (pageBatch.used || items).includes(it) && it.status !== 'waiting' ? `<button data-retry="${i}" style="background:#e6a23c">${it.best ? '重选' : '重试'}</button>` : ''}</div>`).join('')}
      <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        ${running ? `<button id="hy-stop" style="background:#f56c6c">${stopping ? '正在停止…' : '停止'}</button>`
          : `<button id="hy-rerun">全部重选</button>${items.some(isFailed) ? '<button id="hy-retry-failed" style="background:#e6a23c">继续未选的</button>' : ''}`}
        <span class="hy-muted">已上传图片 ${uploadedThumbs().length} 张 · 读到搜索结果 ${groupSeq} 次 · 页面数据 ${netCount}</span></div>
      <p class="hy-muted">「打开货源」在新页面打开选中货源的详情页，核对规格价格后点「设为货源」。</p>`
    box.querySelectorAll('[data-open]').forEach((b) => (b.onclick = () => {
      const it = items[Number(b.dataset.open)]
      send({ type: 'openDetail', key: it.key, url: `https://detail.1688.com/offer/${it.best.offer_id}.html` })
    }))
    const q = (sel) => box.querySelector(sel)
    if (q('#hy-rerun')) q('#hy-rerun').onclick = () => runBatchPage('all')
    if (q('#hy-retry-failed')) q('#hy-retry-failed').onclick = () => runBatchPage('failed')
    if (q('#hy-stop')) q('#hy-stop').onclick = () => { stopping = true; if (choose) choose('stop'); renderBatch(box.querySelector('#hy-msg').innerHTML) }
    if (q('#hy-skip')) q('#hy-skip').onclick = () => choose && choose('skip')
    box.querySelectorAll('[data-retry]').forEach((b) => (b.onclick = () => runBatchPage(Number(b.dataset.retry))))
  }
  const isFailed = (it) => ['error', 'nomatch', 'searching', 'selecting', 'waiting'].includes(it.status)
  let stopping = false

  function uploadedThumbs() {  // the pictures under 「已上传图片 n/10」, in upload order
    const label = [...document.querySelectorAll('div, span, p')].filter((e) => visible(e) &&
      /^已上传图片\s*\d+\s*\/\s*\d+$/.test((e.textContent || '').trim()))
      .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0]  // the label itself
    if (!label) return []
    let box = label.parentElement
    for (let i = 0; i < 5 && box; i++, box = box.parentElement) {
      const lr = label.getBoundingClientRect()  // only the pictures under the label (not the 框选主体 ones on the left)
      const imgs = [...box.querySelectorAll('img')].filter((im) => {
        const r = im.getBoundingClientRect()
        return visible(im) && r.width >= 40 && r.left >= lr.left - 12 && r.top >= lr.top
      })
      if (imgs.length) return imgs.sort((a, b) => {  // reading order: row by row, left to right
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
        return Math.abs(ra.top - rb.top) > ra.height / 2 ? ra.top - rb.top : ra.left - rb.left
      })
    }
    return []
  }

  function realClick(el) {  // like a person: pointer + mouse events on the lower-left of the picture (not the 🗑 corner)
    el.scrollIntoView({ block: 'center' })
    const r = el.getBoundingClientRect()
    const x = r.left + r.width * 0.35, y = r.top + r.height * 0.65
    if (panel) panel.style.visibility = 'hidden'  // our panel may lie over the button
    let target = document.elementFromPoint(x, y)
    if (panel) panel.style.visibility = ''
    if (!target || !(el.contains(target) || target.contains(el))) target = el
    const o = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }
    for (const t of ['pointerover', 'mouseover', 'pointerdown', 'mousedown', 'pointerup', 'mouseup'])
      target.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, o))
    target.dispatchEvent(new MouseEvent('click', o))
  }

  function resultImgs() {  // the result cards' pictures: below the sort bar
    const bar = byText(['综合排序'])
    const top = bar ? bar.getBoundingClientRect().top : -1e9
    return [...document.images].filter((im) => visible(im) && im.getBoundingClientRect().width >= 60 && im.getBoundingClientRect().top > top)
  }
  const resultSig = () => resultImgs().slice(0, 4).map((im) => imgKey(im.currentSrc || im.src)).join('|')

  async function probeCards() {  // last resort: which offer does each card open? (hook.js blocks the opening)
    const cards = []
    for (const im of resultImgs()) {
      const c = cardOf(im) || im.parentElement
      if (c && !cards.includes(c)) { cards.push(c); c.setAttribute('data-hy-img', im.currentSrc || im.src) }
      if (cards.length >= 20) break
    }
    cards.forEach((c, k) => { (c.querySelector('img') || c).setAttribute('data-hy-probe', String(k)) })
    let ids = null
    const onMsg = (e) => { if (e.source === window && e.data && e.data.hy1688 === 'probed') ids = e.data.ids }
    window.addEventListener('message', onMsg)
    window.postMessage({ hy1688: 'probe' }, location.origin)
    for (let i = 0; i < 30 && !ids; i++) await sleep(100)
    window.removeEventListener('message', onMsg)
    for (const [k, id] of Object.entries(ids || {})) cards[Number(k)] && cards[Number(k)].setAttribute('data-hy-offer', id)
  }

  async function scanCards() {  // cards marked by hook.js from the page's own data (links / React / Vue props)
    let done = false
    const onMsg = (e) => { if (e.source === window && e.data && e.data.hy1688 === 'scanned') { done = true; netCount = e.data.net } }
    window.addEventListener('message', onMsg)
    window.postMessage({ hy1688: 'scan' }, location.origin)
    for (let i = 0; i < 20 && !done; i++) await sleep(100)
    window.removeEventListener('message', onMsg)
    if (!document.querySelector('[data-hy-offer]')) await probeCards()
    const seen = new Map()
    for (const el of document.querySelectorAll('[data-hy-offer]')) {
      const id = el.getAttribute('data-hy-offer')
      if (seen.has(id)) continue
      const text = el.innerText || ''
      seen.set(id, { key: id, el, image_url: bigImg(el.getAttribute('data-hy-img') || ''),
        title: el.getAttribute('data-hy-title') || text.split('\n')[0].slice(0, 120),
        price: P.toNum(el.getAttribute('data-hy-price')) ?? P.priceFromText(text),
        sales: P.countNum(el.getAttribute('data-hy-sales')) ?? P.salesFromText(text),
        url: `https://detail.1688.com/offer/${id}.html` })
    }
    return [...seen.values()]
  }

  function looksSelected(th) {  // a selected picture has a coloured border or an active-like class
    let el = th
    for (let k = 0; k < 3 && el; k++, el = el.parentElement) {
      if (/active|selected|checked|current/i.test(el.className && el.className.baseVal == null ? el.className : '')) return true
      const st = getComputedStyle(el)
      if (parseFloat(st.borderTopWidth) >= 2 && !/rgba?\(0, 0, 0, 0\)|transparent/.test(st.borderTopColor)) return true
    }
    return false
  }

  let netCount = 0  // page requests seen by hook.js (diagnosis)
  let groupOwner = -1  // which uploaded picture the latest results belong to (-1: not known)
  async function showPicture(i, th) {  // click picture i and wait for its own results
    for (let attempt = 0; attempt < 2; attempt++) {
      const seq = groupSeq, sig = resultSig()
      realClick(th)
      for (let k = 0; k < 24 && groupSeq === seq && resultSig() === sig; k++) await sleep(500)
      if (groupSeq !== seq) { await sleep(1200); groupOwner = i; return lastGroup }
      if (resultSig() !== sig) { await sleep(1500); groupOwner = i; return [] }  // new results, read from the page
      await closeCropDialog()
    }
    // no new search: the page already showed this picture's results (it was the selected one) —
    // right after the upload (owner not known yet) or when it is marked selected on the page
    if (groupOwner === i || groupOwner === -1 || looksSelected(th)) { groupOwner = i; return groupSeq ? lastGroup : [] }
    return null
  }

  async function bigImageMode() {
    const b = byText(['大图模式'])
    if (b) { realClick(b); await sleep(1000) }
  }

  async function closeCropDialog() {  // 框选主体 may open after an upload: keep the default box
    const c = byText(['确定'])
    if (c && byText(['取消']) && !dialog()) { c.click(); await sleep(500) }
  }

  async function runOne(i, show) {
    const it = pageBatch.items[i]
    const thumbs = uploadedThumbs()
    const ti = it.thumb != null ? it.thumb : i  // its picture under 「已上传图片」
    const th = thumbs[ti]
    if (!th) { await sendStatus(it.key, { status: 'error', note: '没找到这张图的上传结果' }); return }
    document.querySelectorAll('.hy-badge, .hy-best-tag').forEach((x) => x.remove())
    document.querySelectorAll('.hy-best').forEach((x) => x.classList.remove('hy-best'))
    const group = await showPicture(ti, th)
    await closeCropDialog()
    await bigImageMode()
    let cards
    if (group && group.length) cards = collectCards(group)
    else if (group || show) {  // results switched, but not from readable responses: read the cards
      for (let k = 0; k < 4 && resultImgs().length < 8; k++) { window.scrollBy(0, 400); await sleep(500) }
      cards = await scanCards()
      if (!cards.length) cards = groupSeq && show ? collectCards(lastGroup) : collectCards()
      if (!cards.length && !show) {
        Object.assign(it, { status: 'error', note: '读不到搜索结果里的商品编号', thumb: ti })
        return sendStatus(it.key, it)
      }
    } else {
      for (let k = 0; k < 6 && collectCards().length < 8; k++) { window.scrollBy(0, 500); await sleep(600) }
      cards = groupSeq === 0 ? collectCards() : []  // page data seen, but not for this picture
      if (!cards.length) {
        Object.assign(it, { status: 'error', note: groupSeq ? '点了这张图，搜索结果没有切换' : '读不到搜索结果', thumb: ti })
        return sendStatus(it.key, it)
      }
    }
    window.scrollTo(0, 0)
    if (!cards.length) { Object.assign(it, { status: 'nomatch', note: '没有搜索结果', thumb: ti }); return sendStatus(it.key, it) }
    if (!cards.some((c) => c.el)) {  // offers known, cards not matched on the page: mark them from the page
      const sc = await scanCards()
      if (sc.length) cards = sc
    }
    // you choose: 「选为货源」 on a card, or 跳过 / 停止 in the panel
    const prev = { status: it.status, note: it.note }
    Object.assign(it, { status: 'selecting', note: `${cards.length} 个结果，请在卡片上点「选为货源」`, thumb: ti })
    await sendStatus(it.key, { status: 'selecting', note: it.note, thumb: ti })  // detail pages opened now belong to it
    const zoom = document.getElementById('hy-zoom')
    if (zoom && it.image && zoom.dataset.src !== it.image) toggleZoom(it.image)  // large view follows the product being chosen
    let resolvePick
    const picked = new Promise((resolve) => { resolvePick = resolve })
    choose = resolvePick
    renderBatch(`第 ${i + 1} 个：请在下方结果里点「选为货源」，不合适就点「跳过」。`, i)
    if (!pickButtons(cards, (c) => resolvePick(c), it.best && it.best.offer_id)) resolvePick('nocards')
    const pick = await picked
    choose = null
    document.querySelectorAll('.hy-pick').forEach((x) => x.remove())
    if (pick === 'stop') Object.assign(it, prev.status === 'selecting' ? { status: 'waiting', note: '' } : prev)
    else if (pick === 'skip') Object.assign(it, { status: 'nomatch', note: '跳过' })
    else if (pick === 'nocards') Object.assign(it, { status: 'error', note: '结果卡片上放不了「选为货源」按钮' })
    else if (pick.done) Object.assign(it, { status: 'done', note: pick.note || '已设货源', best: pick.best || it.best })
    else Object.assign(it, { status: 'confirm', note: `已选 ¥${pick.price ?? '-'}${pick.sales ? ' · 已售' + pick.sales : ''}`,
      best: { offer_id: pick.key, price: pick.price, title: pick.title } })
    await sendStatus(it.key, { status: it.status, note: it.note, best: it.best, thumb: it.thumb })
  }
  let choose = null  // resolves the choice of the product being chosen: a card, 'skip' or 'stop'


  const visibleFields = () => [...document.querySelectorAll('textarea, input[type=text], input:not([type])')].filter(visible)

  /* give image files to the page's own upload: the page opens its file picker (hook.js hands the files
   * over instead), or, failing that, straight into a file input of the page. Returns how many went. */
  async function giveFiles(files, trigger) {
    let used = null, ready = false
    const onMsg = (e) => {
      if (e.source !== window || !e.data) return
      if (e.data.hy1688 === 'filesUsed') used = e.data
      if (e.data.hy1688 === 'filesReady') ready = true
    }
    window.addEventListener('message', onMsg)
    window.postMessage({ hy1688: 'files', files }, location.origin)
    for (let i = 0; i < 20 && !ready; i++) await sleep(100)  // the page side has the files
    if (trigger && ready) realClick(trigger)
    const before = uploadedThumbs().length
    for (let i = 0; i < 30 && !used && uploadedThumbs().length === before; i++) await sleep(300)  // the page may load its uploader first
    window.removeEventListener('message', onMsg)
    if (used) return used.n
    if (uploadedThumbs().length > before) return uploadedThumbs().length - before
    window.postMessage({ hy1688: 'filesCancel' }, location.origin)
    const input = [...document.querySelectorAll('input[type=file]')].sort((a, b) => (b.multiple ? 1 : 0) - (a.multiple ? 1 : 0))[0]
    if (!input) return 0
    const dt = new DataTransfer()
    for (const f of (input.multiple ? files : files.slice(0, 1))) {
      const blob = await (await fetch(f.dataUrl)).blob()
      dt.items.add(new File([blob], f.name, { type: blob.type || 'image/jpeg' }))
    }
    input.files = dt.files
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return dt.files.length
  }

  async function fillFiles(files) {
    let btn = null
    for (let i = 0; i < 20 && !(btn = byText(['批量图搜'])); i++) await sleep(500)
    const start = uploadedThumbs().length
    let n = await giveFiles(files, btn)
    // the upload may finish a while later: count the pictures under 「已上传图片」
    for (let i = 0; i < 40 && uploadedThumbs().length - start < files.length; i++) {
      await sleep(1000)
      if (i % 5 === 4) await closeCropDialog()
      if (!n && i === 8 && uploadedThumbs().length === start) break  // nothing at all after 9 s
    }
    n = Math.max(n, uploadedThumbs().length - start)
    for (let tries = 0; n < files.length && tries < files.length; tries++) {  // one picture at a time: 点此上传
      let before = uploadedThumbs().length
      for (let i = 0; i < 20 && !byText(['点此上传']); i++) await sleep(500)
      const add = byText(['点此上传'])
      if (!add) break
      const got = await giveFiles(files.slice(n, n + 10), add)
      if (!got) break
      n += got
      for (let i = 0; i < 20 && uploadedThumbs().length <= before; i++) await sleep(500)
      await closeCropDialog()
    }
    return n
  }

  /* mode: false = first run (upload + every picture), 'all' = every picture again (except 已设货源),
   * 'failed' = the failed ones, a number = that one product */
  async function runBatchPage(mode) {
    if (running) return
    running = true; stopping = false
    try { await runBatchPage2(mode) } finally {
      running = false
      const was = stopping; stopping = false
      if (was) renderBatch('<span class="hy-err">已停止。可以点「继续未选的」接着选。</span>')
      else renderBatch(body().querySelector('#hy-msg') ? body().querySelector('#hy-msg').innerHTML : '')
    }
  }

  /* upload the main images of these products (批量图搜); pageBatch.used = the ones 1688 got, in order */
  async function uploadList(list) {
    renderBatch(`正在准备 ${list.length} 个商品主图…`)
    const imgs = await Promise.all(list.map((it) => send({ type: 'fetchImage', item: it }).catch(() => null)))
    list.forEach((it, i) => { if (!imgs[i]) Object.assign(it, { status: 'error', note: '主图读取失败，已跳过' }) })
    for (const it of list) if (it.status === 'error' && /主图读取失败/.test(it.note || '')) await sendStatus(it.key, { status: it.status, note: it.note })
    pageBatch.used = list.filter((it, i) => imgs[i])
    if (!pageBatch.used.length) { renderBatch('<span class="hy-err">商品主图都读取失败。</span>'); return false }
    pageBatch.used.forEach((it) => { it.thumb = null })
    const files = list.map((it, i) => imgs[i] && { name: `${pageBatch.items.indexOf(it) + 1}-${it.kind}-${it.id}.jpg`, dataUrl: imgs[i] }).filter(Boolean)
    // 大图模式 first, then 批量图搜 with the image files (1688 cannot download pictures from other sites)
    await bigImageMode()
    renderBatch(`正在上传 ${files.length} 个商品主图…`)
    const n = await fillFiles(files)
    if (n < files.length) {
      renderBatch(`<span class="hy-err">${n ? `只自动上传了 ${n} 张。` : '没能自动上传主图。'}请把主图下载到电脑，在 1688 点「批量图搜」或「点此上传」选择这些图片，上传完成后点「继续未选的」。</span>` +
        `<br><button id="hy-dl-all">下载 ${files.length} 张主图</button>`)
      body().querySelector('#hy-dl-all').onclick = async () => {
        for (const f of files) {
          const a = document.createElement('a'); a.href = f.dataUrl; a.download = f.name; document.body.appendChild(a); a.click(); a.remove()
          await sleep(300)
        }
      }
      return false
    }
    groupOwner = -1
    await bigImageMode()
    for (let i = 0; i < 40 && uploadedThumbs().length < pageBatch.used.length; i++) { await sleep(1000); if (i % 5 === 4) await closeCropDialog() }
    await closeCropDialog()
    return true
  }

  async function runBatchPage2(again) {
    const items = pageBatch.items
    if (again === false) {  // first run: upload all
      if (!(await uploadList(items.slice()))) return
    } else if (!uploadedThumbs().length) {
      // the page was reloaded or left: 已上传图片 is gone — upload the products still to do again
      const todo = typeof again === 'number' ? [items[again]]
        : items.filter((it) => it.status !== 'done' && (again !== 'failed' || isFailed(it)))
      if (!todo.length) return renderBatch('<span class="hy-msg">没有需要继续的商品。</span>')
      if (!(await uploadList(todo))) return
    }
    const n = uploadedThumbs().length
    if (!n) return renderBatch('<span class="hy-err">没有看到「已上传图片」，请等上传完成后点「继续未选的」。</span>')
    const used = pageBatch.used || items.filter((it) => !(it.status === 'error' && /主图读取失败/.test(it.note || '')))
    for (let j = 0; j < used.length; j++) {
      const i = items.indexOf(used[j])
      if (j >= n) { Object.assign(used[j], { status: 'error', note: '这张图没有上传成功' }); await sendStatus(used[j].key, used[j]); continue }
      if (stopping) break
      if (again !== false && used[j].status === 'done') continue
      if (again === 'failed' && !isFailed(used[j])) continue
      if (typeof again === 'number' && i !== again) continue
      used[j].thumb = j
      renderBatch(`正在打开第 ${j + 1}/${used.length} 个的搜索结果…`, i)
      await runOne(i, false)
      if (j < used.length - 1 && typeof again !== 'number') await sleep(800)
    }
    if (stopping) return
    renderBatch('<span class="hy-msg">全部选完。点「打开货源」核对规格价格并设为货源；要换货源点「重选」。</span>')
  }

  /* ---------- start ---------- */
  const isDetail = () => /detail\.1688\.com\/offer\/|\/offer\/\d+\.html/.test(location.href)

  let started = false
  async function refresh() {
    const s = await chrome.storage.local.get(['current', 'token'])
    if (!s.token) { body().innerHTML = '<p class="hy-muted">请先点浏览器右上角的插件图标登录。</p>'; return }
    if (!started) {  // a 批量 page, or a detail page opened from it (the map is written just after the tab opens)
      for (let i = 0; i < 6 && !tabItem && !pageBatch; i++) {
        tabItem = await send({ type: 'myItem' }).catch(() => null)
        if (!tabItem) pageBatch = await send({ type: 'myBatch' }).catch(() => null)
        if (!tabItem && !pageBatch) await sleep(500)
      }
      if (pageBatch && !isDetail()) {
        started = true
        const fresh = pageBatch.items.every((x) => x.status === 'waiting')
        return fresh ? runBatchPage(false) : renderBatch('上次的选择结果。点「继续未选的」接着选（主图会重新上传）。')
      }
    } else if (tabItem) {
      tabItem = (await send({ type: 'myItem' }).catch(() => null)) || tabItem
    }
    current = tabItem || s.current || null
    if (current) {  // fresh specs + current 货源 from the server
      try { current = { ...current, ...(await call('GET', `/api/ext/item?kind=${current.kind}&id=${current.id}`)) } } catch (e) { /* keep cached */ }
    }
    const first = !started
    started = true
    if (isDetail()) await renderDetail()
    else if (first && tabItem && tabItem.status === 'waiting') return uploadImage()
    else if (first && tabItem && tabItem.status === 'uploaded') return autoVerify()
    else renderSearch()
  }

  chrome.storage.onChanged.addListener((ch) => {
    if (quiet) { quiet = false; return }
    if (pageBatch) {
      if (ch.batch && ch.batch.newValue) {
        pageBatch.items.forEach((it) => {
          const n = ch.batch.newValue.items.find((x) => x.key === it.key)
          if (n && n.status === 'done' && it.status !== 'done') {
            if (it.status === 'selecting' && choose) choose({ done: true, best: n.best, note: n.note })  // set on its detail page: next one
            else Object.assign(it, { status: 'done', note: n.note, best: n.best || it.best })
          }
        })
        if (!running) renderBatch()
      }
      return
    }
    if ((ch.current && !tabItem) || ch.token) refresh()  // a 批量 tab keeps its own product
  })
  refresh()
})()

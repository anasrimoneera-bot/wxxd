/* 1688 货源助手 — runs on 1688 pages in the user's own (logged-in) browser.
 *  - search / image-search result pages: a 「选为货源」 button on every result card; you choose the 货源.
 *  - offer detail pages: read the title, supplier, main image, every SKU price and the 运费, map the
 *    1688 SKUs to our specs, and 「设为货源」 saves it (written to 商品云库 and every store).
 * Nothing is clicked or submitted on 1688 itself: the page is only read. */
(function () {
  const P = window.HY1688
  const send = (msg, tries = 3) => new Promise((resolve, reject) =>
    chrome.runtime.sendMessage(msg, (r) => {
      const le = chrome.runtime.lastError
      if (r && r.ok) return resolve(r.data)
      if (r) return reject(new Error(r.error || '请求失败'))
      // no answer: the background worker was asleep / restarting. Ask again when it is safe (not delivered,
      // or a read); a change (POST) may have gone through, so it is not sent twice
      const notDelivered = le && /Receiving end|establish connection/i.test(le.message)
      const read = msg.type === 'api' ? (msg.method || 'GET') === 'GET' : ['myItem', 'myBatch', 'myShip', 'fetchImage'].includes(msg.type)
      if (tries > 1 && (notDelivered || read)) return setTimeout(() => send(msg, tries - 1).then(resolve, reject), 500)
      reject(new Error('插件后台没有响应' + (le ? `（${le.message}）` : '') + '，请再试一次；一直这样请在 chrome://extensions 里刷新插件'))
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
      if (e.target.id === 'hy-swap-go') { e.stopPropagation(); swapProduct(); return }
      const cp = e.target.closest && e.target.closest('.hy-copy')
      if (cp) {  // 复制商品ID
        e.stopPropagation()
        const done = () => { const t = cp.textContent; cp.textContent = '已复制'; setTimeout(() => { cp.textContent = t }, 1200) }
        navigator.clipboard.writeText(cp.dataset.copy).then(done).catch(() => {
          const ta = document.createElement('textarea'); ta.value = cp.dataset.copy; document.body.appendChild(ta); ta.select()
          try { document.execCommand('copy'); done() } catch (er) { /* nothing */ } ta.remove()
        })
        return
      }
      const img = e.target.closest && e.target.closest('img.hy-zoomable')
      if (!img) return
      e.stopPropagation()
      toggleZoom(img.src)
    })
    panel.addEventListener('keydown', (e) => { if (e.target.id === 'hy-swap-id' && e.key === 'Enter') swapProduct() })
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
    z.innerHTML = `<img src="${esc(src)}"><div>我们的商品图片 · 点击取消放大</div>`
  }

  /* 换商品: work on the product with this 微信小店商品 ID (shown in 订单管理) instead */
  const swapHtml = () => pageBatch ? '' : `<div class="hy-swap"><input id="hy-swap-id" placeholder="换商品：输入微信小店商品 ID" autocomplete="off">
    <button id="hy-swap-go">换</button></div><div id="hy-swap-msg" class="hy-err"></div>`
  async function swapProduct() {
    const inp = panel.querySelector('#hy-swap-id'), msg = panel.querySelector('#hy-swap-msg')
    const id = (inp && inp.value || '').replace(/\D/g, '')
    if (!id) { if (msg) msg.textContent = '请输入商品 ID（订单管理里每个商品下面的「商品ID」）'; return }
    if (msg) { msg.className = 'hy-muted'; msg.textContent = '查找中…' }
    try {
      const it = await call('GET', '/api/ext/by-wx?product_id=' + encodeURIComponent(id))
      tabItem = null  // this tab now works on the new product (not its 批量 one)
      const prev = (await chrome.storage.local.get('current')).current
      if (prev && prev.kind === it.kind && prev.id === it.id) return refresh()  // the same: storage won't change
      await chrome.storage.local.set({ current: it })  // → onChanged → refresh()
    } catch (e) { if (msg) { msg.className = 'hy-err'; msg.textContent = e.message } }
  }

  /* 商品ID (copy it to 换商品 when something goes wrong) and whether it is 上架 */
  const idLine = (it) => it && it.wx_product_id ? `<div class="hy-muted" style="font-size:11px">商品ID <span style="font-family:monospace;color:#606266">${esc(it.wx_product_id)}</span>
    <a class="hy-copy" data-copy="${esc(it.wx_product_id)}" style="color:#409eff;cursor:pointer">复制</a>
    ${it.on_sale === false ? '<span style="color:#e6a23c">· 未上架</span>' : it.on_sale ? '<span style="color:#67c23a">· 上架</span>' : ''}
    ${it.priority ? '<b style="background:#ff6a00;color:#fff;border-radius:3px;padding:0 4px">优先</b>' : ''}
    ${it.exposure ? `<span style="color:#ff6a00">· 曝光 ${it.exposure}</span>` : ''}</div>` : ''

  function curHtml() {
    if (!current) return '<p class="hy-muted">还没有选择商品：点浏览器右上角的插件图标，选一个要找货源的商品。</p>' + swapHtml()
    return swapHtml() + `<div class="hy-cur"><img class="hy-zoomable" title="点击放大" src="${esc(current.image)}"><div style="flex:1;min-width:0">
      <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(current.title)}">${esc(current.title)}</div>
      <div class="hy-muted">${tabItem ? '<b style="color:#ff6a00">批量</b> · ' : ''}${esc(current.store)} · ${current.specs.length} 个规格 · 售价 ¥${yuan(current.price_min)}</div>${idLine(current)}</div></div>`
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
  function pickButtons(cards, onPick, chosen, label = '选为货源') {
    document.querySelectorAll('.hy-pick').forEach((x) => x.remove())
    document.querySelectorAll('.hy-best').forEach((x) => x.classList.remove('hy-best'))
    let n = 0
    for (const c of cards) {
      if (!c.el) continue
      if (getComputedStyle(c.el).position === 'static') c.el.style.position = 'relative'
      const b = document.createElement('button')
      b.className = 'hy-pick' + (chosen === c.key ? ' on' : '')
      b.textContent = chosen === c.key ? '已选为货源' : label
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
  function pageTextDeep() {  // innerText plus the text inside web components (shadow roots)
    let t = document.body.innerText || ''
    const walk = (root) => {
      for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { t += '\n' + (el.shadowRoot.textContent || ''); walk(el.shadowRoot) }
    }
    walk(document)
    return t
  }
  function readOffer() {
    const scripts = [...document.scripts].map((s) => s.textContent).join('\n')
    const text = pageTextDeep()
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
      freight: P.freightFromText(text) ?? P.freightFromScripts(scripts), moq: moqM ? Number(moqM[1]) : null, detail_url: location.href.split('#')[0] }
  }

  /* 1688 规格 can be dozens: a <select> opens a list with a search box instead (the select keeps the value,
   * so everything reading it stays the same). Words separated by spaces must all be in the option. */
  const normSpec = (t) => String(t || '').toLowerCase().replace(/[\s/|，,·・\-_（）()【】\[\]]+/g, '')
  let pickerClose = null
  function openSpecPicker(sel) {
    if (pickerClose) pickerClose()
    const r = sel.getBoundingClientRect()
    const pop = document.createElement('div')
    pop.id = 'hy-picker'
    const width = Math.min(Math.max(r.width, 380), window.innerWidth - 16)
    pop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - width - 8)) + 'px'
    pop.style.width = width + 'px'
    const below = window.innerHeight - r.bottom > 260
    pop.style[below ? 'top' : 'bottom'] = (below ? r.bottom + 2 : window.innerHeight - r.top + 2) + 'px'
    pop.innerHTML = '<input placeholder="搜索 1688 规格（多个词用空格分开）"><div class="hy-pl"></div>'
    document.body.appendChild(pop)
    const input = pop.querySelector('input'), list = pop.querySelector('.hy-pl')
    const opts = [...sel.options].map((o) => ({ value: o.value, text: o.textContent, key: normSpec(o.textContent) }))
    let shown = [], active = 0
    const draw = () => {
      const words = input.value.trim().split(/\s+/).map(normSpec).filter(Boolean)
      shown = opts.filter((o) => !words.length || words.every((w) => o.key.includes(w)))
      if (active >= shown.length) active = 0
      list.innerHTML = shown.length ? shown.map((o, k) => `<div class="hy-po${k === active ? ' on' : ''}${o.value === sel.value ? ' cur' : ''}" data-k="${k}">${esc(o.text)}</div>`).join('')
        : '<div class="hy-pn">没有找到，换个词试试</div>'
      const on = list.querySelector('.hy-po.on'); if (on) on.scrollIntoView({ block: 'nearest' })
    }
    const choose = (o) => {
      if (!o) return
      sel.value = o.value
      sel.dispatchEvent(new Event('change', { bubbles: true }))
      close()
    }
    const onDoc = (e) => { if (!pop.contains(e.target) && e.target !== sel) close() }
    function close() { pop.remove(); document.removeEventListener('mousedown', onDoc, true); pickerClose = null }
    pickerClose = close
    active = Math.max(0, opts.findIndex((o) => o.value === sel.value))
    input.oninput = () => { active = 0; draw() }
    input.onkeydown = (e) => {
      if (e.key === 'ArrowDown') { active = Math.min(active + 1, shown.length - 1); draw(); e.preventDefault() }
      else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); draw(); e.preventDefault() }
      else if (e.key === 'Enter') { choose(shown[active]); e.preventDefault() }
      else if (e.key === 'Escape') close()
    }
    list.onmousedown = (e) => { const d = e.target.closest('.hy-po'); if (d) { e.preventDefault(); choose(shown[Number(d.dataset.k)]) } }
    setTimeout(() => document.addEventListener('mousedown', onDoc, true), 0)
    draw()
    input.focus()
  }
  function searchableSelect(sel) {
    if (sel.options.length < 8) return  // a short list: the normal dropdown is fine
    sel.addEventListener('mousedown', (e) => { e.preventDefault(); openSpecPicker(sel) })
    sel.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') { e.preventDefault(); openSpecPicker(sel) } })
  }

  /* ---------- 1688 上架: list the 商品云库 product with this 1688 offer (images, specs, prices from 1688) ---------- */
  let showLinkOnly = false  // 只设为货源 (the old way), chosen on the page
  function detailImagesFromPage() {  // the 详情 pictures already drawn on the page
    const out = []
    for (const box of document.querySelectorAll('[id*="detail" i], [class*="detail" i], [id*="desc" i], [class*="desc" i]')) {
      if (box.closest('#hy-panel')) continue
      for (const im of box.querySelectorAll('img')) {
        const u = P.fullImage(im.getAttribute('data-lazyload-src') || im.getAttribute('data-src') || im.currentSrc || im.src)
        if (u && !out.includes(u) && (im.naturalWidth >= 300 || im.width >= 300 || /lazyload/.test(im.outerHTML))) out.push(u)
      }
    }
    return out.slice(0, 50)
  }
  async function offerDetailImages(scripts) {
    const url = P.descUrl(scripts)
    if (url) {
      try {
        const imgs = P.imagesFromHtml(await send({ type: 'fetchText', url: url.replace(/^\/\//, 'https://') }))
        if (imgs.length) return imgs
      } catch (e) { /* the page itself */ }
    }
    return detailImagesFromPage()
  }
  async function renderOfferPublish(o) {
    const box = body()
    const scripts = [...document.scripts].map((x) => x.textContent).join('\n')
    const skus = P.offerSkus(scripts, o.price)
    const list = skus.length ? skus : [{ name: '默认', sku_id: 'default', price: o.price, stock: null, attrs: [], image: '' }]
    const images = P.offerImages(scripts)
    if (!images.length && o.image_url) images.push(o.image_url)
    const pageVideo = [...document.querySelectorAll('video')].map((v) => v.currentSrc || v.src || (v.querySelector('source') || {}).src || '')
      .find((u) => /^https?:\/\/.+\.(mp4|mov)/i.test(u))
    const video = P.offerVideo(scripts) || pageVideo || ''
    box.innerHTML = curHtml() + '<p class="hy-muted">正在读取 1688 商品详情…</p>'
    const [detailImgs, stores, ap, saved] = await Promise.all([
      offerDetailImages(scripts), call('GET', '/api/stores').catch(() => []),
      call('GET', '/api/products/auto-price').catch(() => ({ margin: 2, add: 0 })), chrome.storage.local.get('pubStores')])
    const lastStores = new Set(saved.pubStores || [])
    const margin = Number(ap.margin) || 2, add = Number(ap.add) || 0
    const money = (v) => (Math.round(v * 100) / 100).toFixed(2)
    const okStores = stores.filter((st) => st.config_status === 'configured')
    box.innerHTML = curHtml() + `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <img src="${esc(images[0] || o.image_url)}" style="width:48px;height:48px;object-fit:cover;border-radius:4px">
        <div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">1688：${esc(o.title)}</div>
        <div class="hy-muted">${esc(o.supplier)} · ${list.length} 个规格 · 主图 ${images.length} 张 · 详情图 ${detailImgs.length} 张${detailImgs.length ? '' : '（用云库的）'} · ${video ? '<b style="color:#67c23a">有主图视频</b>' : '没有视频'}</div></div></div>
      ${video ? '<div class="hy-muted" style="font-size:12px">视频会先上传到微信（微信不能直接用 1688 的视频链接）；超过 180 秒或 500MB 微信不收，会不带视频上架并说明原因。</div>' : ''}
      <div class="hy-msg" style="margin:4px 0">用这个 1688 商品上架：主图、详情图、规格和价格用 1688 的，标题和类目用云库商品的。</div>
      <div style="display:flex;gap:6px;align-items:center;margin:4px 0;flex-wrap:wrap">
        <label class="hy-muted" style="display:flex;gap:3px;align-items:center;margin:0"><input type="checkbox" id="hy-o-all" checked style="width:auto;margin:0">全选规格</label>
        <span class="hy-muted">运费 ¥</span><input id="hy-o-freight" type="number" step="0.01" min="0" value="${o.freight ?? ''}" placeholder="必填" style="width:70px${o.freight == null ? ';border-color:#f56c6c' : ''}">
        <span class="hy-muted">售价 = (1688价 + 运费) × ${margin} + ${money(add / 100)}（+ 店铺优惠券）</span></div>
      <table><tr><th></th><th>1688 规格</th><th>1688价</th><th>库存</th><th>预计售价</th></tr>
      ${list.map((k, i) => `<tr data-i="${i}"><td style="width:20px"><input type="checkbox" class="hy-o-pick" ${k.stock === 0 ? '' : 'checked'} style="width:auto;margin:0"></td>
        <td><div style="display:flex;gap:4px;align-items:center">${k.image ? `<img class="hy-zoomable" src="${esc(k.image)}" style="width:28px;height:28px;object-fit:cover;border-radius:3px;flex:none">` : ''}<span>${esc(k.name)}</span></div></td>
        <td>¥${k.price ?? '-'}</td><td>${k.stock ?? '-'}</td><td class="hy-o-sale"></td></tr>`).join('')}</table>
      <div style="margin-top:8px"><b>上架到店铺</b>${okStores.length ? '' : '<span class="hy-err">（没有已完成发布配置的店铺，请先在网站「发布配置」里配置）</span>'}</div>
      <div style="display:flex;gap:4px 12px;flex-wrap:wrap;margin:4px 0">${stores.map((st) => `<label style="display:flex;gap:3px;align-items:center;margin:0${st.config_status === 'configured' ? '' : ';color:#c0c4cc'}">
        <input type="checkbox" class="hy-o-store" value="${st.id}" ${st.config_status !== 'configured' ? 'disabled' : lastStores.has(st.id) || okStores.length === 1 ? 'checked' : ''} style="width:auto;margin:0">${esc(st.name)}${st.config_status === 'configured' ? '' : '（未配置）'}</label>`).join('')}</div>
      <div style="display:flex;gap:6px;margin-top:8px;align-items:center"><button class="hy-ok" id="hy-o-go">上架到小店</button>
        <a id="hy-o-link" class="hy-muted" style="cursor:pointer;font-size:12px">只设为货源，不上架</a></div>
      <div id="hy-msg" style="margin-top:6px"></div>`
    const q = (sel) => box.querySelector(sel)
    const freight = () => Math.max(0, parseFloat(q('#hy-o-freight').value) || 0)
    const picked = () => [...box.querySelectorAll('.hy-o-pick')].map((c, i) => c.checked ? list[i] : null).filter(Boolean)
    const storesPicked = () => [...box.querySelectorAll('.hy-o-store')].filter((c) => c.checked).map((c) => Number(c.value))
    const draw = () => {
      box.querySelectorAll('tr[data-i]').forEach((tr) => {
        const k = list[Number(tr.dataset.i)]
        tr.querySelector('.hy-o-sale').textContent = k.price != null ? `¥${money((k.price + freight()) * margin + add / 100)}` : '-'
        tr.style.opacity = tr.querySelector('.hy-o-pick').checked ? '' : '.45'
      })
      const n = picked().length, m = storesPicked().length
      q('#hy-o-go').textContent = `上架到小店（${n} 个规格 · ${m} 个店铺）`
      q('#hy-o-go').disabled = !n || !m
      q('#hy-o-all').checked = n === list.length
    }
    box.querySelectorAll('.hy-o-pick, .hy-o-store').forEach((c) => (c.onchange = draw))
    q('#hy-o-all').onchange = (e) => { box.querySelectorAll('.hy-o-pick').forEach((c) => (c.checked = e.target.checked)); draw() }
    q('#hy-o-freight').oninput = () => { q('#hy-o-freight').style.borderColor = ''; draw() }
    q('#hy-o-link').onclick = () => { showLinkOnly = true; renderDetail() }
    draw()
    q('#hy-o-go').onclick = async () => {
      const msg = q('#hy-msg')
      if (q('#hy-o-freight').value === '') { q('#hy-o-freight').style.borderColor = '#f56c6c'; msg.innerHTML = '<span class="hy-err">请填写运费（包邮填 0）</span>'; return }
      const sids = storesPicked()
      chrome.storage.local.set({ pubStores: sids })
      const btn = q('#hy-o-go'); btn.disabled = true; btn.textContent = '正在提交…'
      try {
        const r = await call('POST', '/api/ext/publish-offer', { id: current.id, store_ids: sids, offer: {
          offer_id: o.offer_id, title: o.title, detail_url: o.detail_url, supplier: o.supplier, image_url: images[0] || o.image_url,
          freight: freight(), images, detail_images: detailImgs, video_url: video,
          skus: picked().map((k) => ({ key: String(k.sku_id), name: k.name, attrs: k.attrs || [], price: k.price, stock: k.stock, image: k.image || '' })) } })
        await setStatus({ status: 'done', note: `已提交上架 ${r.tasks.length} 个店铺`, best: { offer_id: o.offer_id, price: Math.min(...picked().map((k) => k.price)) + freight(), title: o.title } })
        msg.innerHTML = (r.skipped.length ? `<div class="hy-err">${r.skipped.map(esc).join('<br>')}</div>` : '') +
          `<div id="hy-o-prog">${r.tasks.map((t) => `<div data-t="${t.id}">${esc(t.store)}：排队中…</div>`).join('')}</div>`
        btn.textContent = r.tasks.length ? '已提交，上架中…' : '没有可以上架的店铺'
        if (r.tasks.length) watchTasks(r.tasks)
      } catch (e) { btn.disabled = false; draw(); msg.innerHTML = `<span class="hy-err">${esc(e.message)}</span>` }
    }
  }
  async function watchTasks(tasks) {  // each store's 发布任务 until it is done
    const left = new Set(tasks.map((t) => t.id))
    for (let n = 0; n < 120 && left.size; n++) {
      await sleep(3000)
      for (const t of tasks) {
        if (!left.has(t.id)) continue
        try {
          const it = ((await call('GET', `/api/tasks/${t.id}/items`)).items || [])[0]
          const el = document.querySelector(`#hy-o-prog [data-t="${t.id}"]`)
          if (!it || !el) continue
          const label = { pending: '排队中…', running: '上架中…', success: '✅ 已提交微信审核', failed: '❌ 失败', skipped: '⏭ 跳过' }[it.status] || it.status
          el.innerHTML = `${esc(t.store)}：${label}${it.message && it.status !== 'pending' && it.status !== 'running' ? ` <span class="hy-muted">${esc(it.message)}</span>` : ''}`
          if (!['pending', 'running'].includes(it.status)) left.delete(t.id)
        } catch (e) { /* next round */ }
      }
    }
    const b = body().querySelector('#hy-o-go')
    if (b) b.textContent = '上架完成'
    if (tabItem) setTimeout(() => send({ type: 'closeMe' }).catch(() => {}), 4000)  // 批量: back to the 找品 page
  }

  async function renderDetail() {
    const box = body()
    if (!current) { box.innerHTML = curHtml(); return }
    const o = readOffer()
    if (!o.offer_id) { box.innerHTML = curHtml() + '<p class="hy-err">读不到商品编号</p>'; return }
    if (current.kind === 'library' && !showLinkOnly) return renderOfferPublish(o)
    const map = o.skus.length ? P.matchSpecs(current.specs, o.skus) : {}
    const memKey = `${current.kind}:${current.id}:${o.offer_id}`  // your own choices for this pair are remembered
    const { specMaps = {}, specQty = {} } = await chrome.storage.local.get(['specMaps', 'specQty'])
    const saved = specMaps[memKey] || {}
    const qtys = specQty[memKey] || {}  // 数量倍数: how many 1688 pieces make one of our spec (e.g. 3把装 = 3)
    const money = (v) => Math.round(v * 100) / 100
    for (const k in saved) if (saved[k] < o.skus.length) map[k] = saved[k]
    const minPrice = o.skus.length ? Math.min(...o.skus.map((x) => x.price ?? Infinity)) : o.price
    const opts = (sel) => o.skus.map((s, i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${esc(s.name)} ¥${s.price ?? '-'}</option>`).join('')
    const rows = current.specs.map((sp) => {
      const i = map[sp.key] ?? -1
      const unit = i >= 0 ? o.skus[i].price : (isFinite(minPrice) ? minPrice : o.price)
      const q = Number(qtys[sp.key]) > 0 ? Number(qtys[sp.key]) : 1
      return `<tr data-key="${esc(sp.key)}" data-unit="${unit ?? ''}" ${i < 0 && o.skus.length ? 'style="background:#fff7e6"' : ''}><td style="width:34%">
        <div style="display:flex;gap:4px;align-items:center">${current.kind === 'store' ? `<input type="checkbox" class="hy-pick-spec" style="width:auto;margin:0;flex:none" title="选中，可批量删除">` : ''}${sp.image ? `<img class="hy-zoomable" title="点击放大" src="${esc(sp.image)}" style="width:32px;height:32px;object-fit:cover;border-radius:3px;flex:none">` : ''}
        <span>${esc(sp.label)}</span></div>
        ${current.kind === 'store' ? `<a class="hy-del" data-key="${esc(sp.key)}" title="货源没有这个规格：微信小店里这个规格库存改为 0（下架），商品管理里不再显示" style="color:#f56c6c;font-size:12px;cursor:pointer">删除规格</a>` : ''}</td>
        <td>${o.skus.length ? `<select class="hy-sku"><option value="-1">（不对应）</option>${opts(i)}</select>` : ''}</td>
        <td style="width:42px"><input class="hy-qty" type="number" step="1" min="1" value="${q}" title="数量倍数：我们的 1 份 = 1688 的几件"></td>
        <td style="width:64px"><input class="hy-price" type="number" step="0.01" min="0" value="${unit != null ? money(unit * q + (o.freight || 0)) : ''}"></td></tr>`
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
      ${current.kind === 'store' ? `<div id="hy-bulk" style="display:flex;gap:6px;align-items:center;margin:4px 0">
        <label class="hy-muted" style="display:flex;gap:3px;align-items:center;margin:0"><input type="checkbox" id="hy-pick-all" style="width:auto;margin:0">全选</label>
        <button id="hy-pick-unmatched" style="padding:2px 8px;background:#909399">选未对应的</button>
        <button id="hy-del-picked" style="padding:2px 8px;background:#f56c6c" disabled>删除所选规格（0）</button></div>` : ''}
      <table><tr><th>我们的规格</th><th>1688 规格</th><th title="我们的 1 份 = 1688 的几件">数量</th><th>货源价</th></tr>${rows}</table>
      <p class="hy-muted" style="margin:2px 0 6px">货源价 = 1688 单价 × 数量 + 运费（比如我们卖「3把装」，数量填 3）。</p>
      <div style="display:flex;gap:6px;align-items:center"><span class="hy-muted" style="white-space:nowrap">运费 ¥</span>
        <input id="hy-freight" type="number" step="0.01" min="0" value="${o.freight ?? ''}" placeholder="必填，包邮填 0" style="width:90px${o.freight == null ? ';border-color:#f56c6c' : ''}">
        <span class="hy-muted">运费已算进每个规格的货源价</span></div>
      <div style="display:flex;gap:6px;margin-top:10px"><button class="hy-ok" id="hy-save">设为货源</button></div>
      <div id="hy-msg" style="margin-top:6px"></div>
      ${current.source && current.source.offer_id ? `<p class="hy-muted">当前货源：${esc(current.source.offer_id)}（¥${yuan(current.source.price)}），设为货源会替换它。</p>` : ''}`
    const setRow = (tr, idx) => {
      const sel = tr.querySelector('.hy-sku'); if (!sel) return
      sel.value = String(idx)
      const sku = o.skus[idx]
      if (sku && sku.price != null) { tr.dataset.unit = sku.price; recalc(tr) }
      tr.style.background = idx < 0 ? '#fff7e6' : ''
    }
    const freightNow = () => Math.max(0, parseFloat((box.querySelector('#hy-freight') || {}).value) || 0)
    function recalc(tr) {  // 货源价 = 单价 × 数量 + 运费
      const unit = parseFloat(tr.dataset.unit), q = Math.max(1, parseFloat(tr.querySelector('.hy-qty').value) || 1)
      if (isFinite(unit)) tr.querySelector('.hy-price').value = money(unit * q + freightNow())
    }
    const remember = async () => {
      const m = {}
      const qm = {}
      box.querySelectorAll('tr[data-key]').forEach((tr) => {
        const sel = tr.querySelector('.hy-sku'); if (sel) m[tr.dataset.key] = Number(sel.value)
        const q = parseFloat(tr.querySelector('.hy-qty').value); if (q > 1) qm[tr.dataset.key] = q
      })
      const st = await chrome.storage.local.get(['specMaps', 'specQty'])
      const all = st.specMaps || {}, allQ = st.specQty || {}
      all[memKey] = m
      allQ[memKey] = qm
      for (const x of [all, allQ]) { const keys = Object.keys(x); if (keys.length > 300) delete x[keys[0]] }
      await chrome.storage.local.set({ specMaps: all, specQty: allQ })
    }
    const countUnmatched = () => {
      const n = [...box.querySelectorAll('.hy-sku')].filter((x) => x.value === '-1').length
      const el = box.querySelector('#hy-unmatched')
      if (el) el.innerHTML = n ? `<b style="color:#e6a23c">${n} 个规格没对应上（橙色），请选一下</b>` : '全部对应上了，核对价格即可。'
    }
    box.querySelectorAll('.hy-sku').forEach((s) => (s.onchange = () => { setRow(s.closest('tr'), Number(s.value)); countUnmatched(); remember() }))
    box.querySelectorAll('.hy-sku, #hy-all').forEach(searchableSelect)
    const order = box.querySelector('#hy-order')
    if (order) order.onclick = () => { box.querySelectorAll('tr[data-key]').forEach((tr, i) => setRow(tr, i < o.skus.length ? i : -1)); countUnmatched(); remember() }
    const all = box.querySelector('#hy-all')
    if (all) all.onchange = () => { if (all.value !== '') { box.querySelectorAll('tr[data-key]').forEach((tr) => setRow(tr, Number(all.value))); all.value = ''; countUnmatched(); remember() } }
    box.querySelectorAll('.hy-qty').forEach((x) => (x.oninput = () => { recalc(x.closest('tr')); remember() }))
    box.querySelectorAll('.hy-price').forEach((x) => (x.oninput = () => {  // a price typed by hand: keep it as 单价 × 数量 + 运费
      const tr = x.closest('tr'), q = Math.max(1, parseFloat(tr.querySelector('.hy-qty').value) || 1), v = parseFloat(x.value)
      if (v > 0) tr.dataset.unit = Math.max(v - freightNow(), 0) / q
    }))
    box.querySelector('#hy-freight').oninput = () => {  // the 运费 goes into every spec's 货源价
      box.querySelector('#hy-freight').style.borderColor = ''
      box.querySelectorAll('tr[data-key]').forEach(recalc)
    }
    /* 删除所选规格: several specs the 货源 does not have, at once */
    const picks = () => [...box.querySelectorAll('.hy-pick-spec')].filter((c) => c.checked)
    const updatePicked = () => {
      const b = box.querySelector('#hy-del-picked'); if (!b) return
      const n = picks().length
      b.textContent = `删除所选规格（${n}）`; b.disabled = !n
      const all = box.querySelector('#hy-pick-all'), boxes = box.querySelectorAll('.hy-pick-spec')
      if (all) all.checked = boxes.length > 0 && n === boxes.length
    }
    box.querySelectorAll('.hy-pick-spec').forEach((c) => (c.onchange = updatePicked))
    if (box.querySelector('#hy-pick-all')) box.querySelector('#hy-pick-all').onchange = (e) => {
      box.querySelectorAll('.hy-pick-spec').forEach((c) => { c.checked = e.target.checked }); updatePicked()
    }
    if (box.querySelector('#hy-pick-unmatched')) box.querySelector('#hy-pick-unmatched').onclick = () => {
      box.querySelectorAll('tr[data-key]').forEach((tr) => {
        const sel = tr.querySelector('.hy-sku'), c = tr.querySelector('.hy-pick-spec')
        if (c) c.checked = !!sel && sel.value === '-1'
      })
      updatePicked()
    }
    const removeSpecs = async (keys, labels) => {
      const msg = box.querySelector('#hy-msg')
      const r = await call('POST', '/api/ext/sku-remove', { kind: current.kind, id: current.id, sku_ids: keys })
      const gone = new Set(r.removed || [])
      current.specs = current.specs.filter((x) => !gone.has(x.key))
      box.querySelectorAll('tr[data-key]').forEach((tr) => { if (gone.has(tr.dataset.key)) tr.remove() })
      countUnmatched(); updatePicked()
      msg.innerHTML = `<span class="hy-msg">已删除 ${gone.size} 个规格（微信小店库存已改为 0）：${esc(labels.filter((l, i) => gone.has(keys[i])).join('、'))}</span>` +
        ((r.failed || []).length ? `<br><span class="hy-err">${r.failed.length} 个没删掉：${esc(r.failed[0].error)}</span>` : '')
    }
    if (box.querySelector('#hy-del-picked')) box.querySelector('#hy-del-picked').onclick = async () => {
      const rows = picks().map((c) => c.closest('tr'))
      if (!rows.length) return
      const labels = rows.map((tr) => tr.querySelector('span').innerText)
      if (!confirm(`删除这 ${rows.length} 个规格？\n${labels.join('\n')}\n\n微信小店里这些规格的库存会改为 0（下架，买家不能再下单），商品管理里不再显示。`)) return
      const b = box.querySelector('#hy-del-picked'); b.disabled = true; b.textContent = '删除中…'
      try { await removeSpecs(rows.map((tr) => tr.dataset.key), labels) } catch (e) {
        box.querySelector('#hy-msg').innerHTML = `<span class="hy-err">${esc(e.message)}</span>`; updatePicked()
      }
    }
    box.querySelectorAll('.hy-del').forEach((a) => (a.onclick = async () => {  // the 货源 has no such spec
      const tr = a.closest('tr'), label = tr.querySelector('span').innerText
      if (!confirm(`删除规格「${label}」？\n微信小店里这个规格的库存会改为 0（下架，买家不能再下单），商品管理里不再显示。`)) return
      a.textContent = '删除中…'
      try {
        await removeSpecs([a.dataset.key], [label])
      } catch (e) { a.textContent = '删除规格'; box.querySelector('#hy-msg').innerHTML = `<span class="hy-err">${esc(e.message)}</span>` }
    }))
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
      if (fr === '' || !(Number(fr) >= 0)) {  // never leave the 运费 out of the 采购成本
        box.querySelector('#hy-freight').style.borderColor = '#f56c6c'
        msg.innerHTML = '<span class="hy-err">没读到运费，请填写运费（包邮填 0）再设为货源</span>'
        return
      }
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
        const ap = d.auto_price || []  // 自动改价 (preset 公式, when turned on)
        const apHtml = ap.map((r) => r.ok
          ? `<br><span class="hy-msg">已自动改价${r.store ? '（' + esc(r.store) + '）' : ''}：¥${yuan(r.min)}${r.max !== r.min ? ' - ¥' + yuan(r.max) : ''}</span>`
          : `<br><span class="hy-err">自动改价失败：${esc(r.error || '')}</span>`).join('')
        msg.innerHTML = '<span class="hy-msg">已设为货源，商品云库和各店铺里的同一商品已同步。</span>' + apHtml +
          `<br><span class="hy-muted">${ap.length ? 3 : 1} 秒后自动关闭本页…</span>`
        await setStatus({ status: 'done', note: `货源 ¥${price}` + (ap.some((r) => r.ok) ? ' · 已自动改价' : ''), best: { offer_id: o.offer_id, price, title: o.title } })
        setTimeout(() => send({ type: 'closeMe' }).catch(() => {}), ap.length ? 3200 : 1200)  // done: close this page
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
  const STATUS = { waiting: '等待', searching: '打开中', selecting: '请选择货源', confirm: '已选，待设货源', done: '已设货源', found: '有货源（待第二阶段精选）', nomatch: '已跳过', error: '出错' }
  const stage1 = () => !!(pageBatch && pageBatch.query && Number(pageBatch.query.stage) === 1 ||
    pageBatch && (!pageBatch.query || !pageBatch.query.stage))  // 第一阶段 (粗找): the default
  // products with 曝光 (优先) are not only checked in 第一阶段: their 货源 is chosen right away
  const quick = (it) => stage1() && !(it && (it.priority || (it.exposure || 0) > 0))
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
          ${idLine(it)}
          <div class="hy-muted" ${i === active ? 'style="color:#ff6a00;font-weight:600"' : ''}>${STATUS[it.status] || it.status}${it.note ? ' · ' + esc(it.note) : ''}</div></div>
        ${it.status === 'selecting' && choose ? `<span style="display:flex;flex-direction:column;gap:4px">${quick(it) ? '<button id="hy-found" class="hy-ok">有货源</button>' : ''}<button id="hy-skip" style="background:#909399">跳过</button></span>` : ''}
        ${it.best && it.status !== 'selecting' ? `<button data-open="${i}" ${it.status === 'done' ? 'class="hy-ok"' : ''}>${it.status === 'done' ? '已设' : '打开货源'}</button>` : ''}
        ${!running && it.status !== 'done' && (pageBatch.used || items).includes(it) && it.status !== 'waiting' ? `<button data-retry="${i}" style="background:#e6a23c">${it.best ? '重选' : '重试'}</button>` : ''}</div>`).join('')}
      <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        ${running ? `<button id="hy-stop" style="background:#f56c6c">${stopping ? '正在停止…' : '停止'}</button>`
          : `<button id="hy-rerun">全部重选</button>${items.some(isFailed) ? '<button id="hy-retry-failed" style="background:#e6a23c">继续未选的</button>' : ''}`}
        ${isChannel() ? '' : `<span class="hy-muted">已上传图片 ${uploadedThumbs().length} 张 · 读到搜索结果 ${groupSeq} 次 · 页面数据 ${netCount}</span>`}</div>
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
    if (q('#hy-found')) q('#hy-found').onclick = () => choose && choose('found')
    box.querySelectorAll('[data-retry]').forEach((b) => (b.onclick = () => runBatchPage(Number(b.dataset.retry))))
  }
  /* the batch is finished when every product is 已设货源 or 跳过 (errors do not hold it up: they stay
   * in the list of products without a 货源 for a later batch) -> the next 10 */
  const finished = () => pageBatch && pageBatch.items.every((it) => ['done', 'found', 'nomatch', 'error'].includes(it.status))
  let nextTimer = null
  function maybeNextBatch() {
    if (!finished() || nextTimer || running) return
    let left = 5
    const tick = async () => {
      if (!nextTimer) return
      if (left <= 0) {
        nextTimer = null
        renderBatch('正在取下一批商品…')
        nextTimer = 'loading'  // asked once: no second request while the next page opens
        try {
          const r = await send({ type: 'nextBatch' })
          if (r && r.none) renderBatch('<span class="hy-msg">这一批完成了，没有更多还没有货源的商品了。</span>')
          else renderBatch(`正在打开下一批（${r && r.count} 个）…`)
        } catch (e) { nextTimer = null; renderBatch(`<span class="hy-err">取下一批失败：${esc(e.message)}</span> <button id="hy-retry-next">重试</button>`)
          const b = body().querySelector('#hy-retry-next'); if (b) b.onclick = () => maybeNextBatch() }
        return
      }
      renderBatch(`<span class="hy-msg">这一批完成了。${left} 秒后自动开始下一批（10 个还没有货源的商品）…</span> <button id="hy-no-next" style="background:#909399">不继续</button>`)
      const b = body().querySelector('#hy-no-next')
      if (b) b.onclick = () => { clearTimeout(nextTimer); nextTimer = null; renderBatch('已取消自动下一批。要继续请在插件弹窗里重新选择商品。') }
      left--
      if (nextTimer) nextTimer = setTimeout(tick, 1000)
    }
    nextTimer = setTimeout(tick, 0)
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
  async function settle() {  // the new results are drawn: the first pictures stop changing (at most ~1.2 s)
    let last = resultSig()
    for (let k = 0; k < 8; k++) {
      await sleep(150)
      const now = resultSig()
      if (now && now === last && resultImgs().length >= 4) return
      last = now
    }
  }
  async function showPicture(i, th) {  // click picture i and wait for its own results
    // its results are on the page already (the selected picture): no click, no waiting for a change that never comes
    if (groupOwner === i && resultImgs().length) return groupSeq ? lastGroup : []
    for (let attempt = 0; attempt < 2; attempt++) {
      const seq = groupSeq, sig = resultSig()
      realClick(th)
      for (let k = 0; k < 24 && groupSeq === seq && resultSig() === sig; k++) {
        await sleep(250)
        // nothing changes but the picture is now the selected one: its results were already shown
        if (k === 10 && looksSelected(th) && resultImgs().length && (groupOwner === i || groupOwner === -1)) break
      }
      if (groupSeq !== seq) { await settle(); groupOwner = i; return lastGroup }
      if (resultSig() !== sig) { await settle(); groupOwner = i; return [] }  // new results, read from the page
      if (groupOwner === -1 || (looksSelected(th) && groupOwner === i)) break  // nothing new: these are its results
      await closeCropDialog()
    }
    // no new search: the page already showed this picture's results (it was the selected one) —
    // right after the upload (owner not known yet) or when it is marked selected on the page
    if (groupOwner === i || groupOwner === -1 || looksSelected(th)) { groupOwner = i; return groupSeq ? lastGroup : [] }
    return null
  }

  let bigDone = false
  async function bigImageMode() {  // once per page (not before every product)
    if (bigDone) return
    bigDone = true
    const b = byText(['大图模式'])
    if (b) { realClick(b); await sleep(600) }
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
    else if (group && quick(it)) cards = collectCards()  // 第一阶段: you only look at the results, no card is picked
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
    return pickAmong(i, it, cards, ti)
  }

  /* you choose among these result cards: 「选为货源」 on a card, or 跳过 / 停止 in the panel */
  async function pickAmong(i, it, cards, ti) {
    window.scrollTo(0, 0)
    if (!cards.length && !quick(it)) { Object.assign(it, { status: 'nomatch', note: '没有搜索结果', thumb: ti }); return sendStatus(it.key, it) }
    if (!quick(it) && !cards.some((c) => c.el)) {  // offers known, cards not matched on the page: mark them from the page
      const sc = await scanCards()
      if (sc.length) cards = sc
    }
    // you choose: 「选为货源」 on a card, or 跳过 / 停止 in the panel
    const prev = { status: it.status, note: it.note }
    Object.assign(it, { status: 'selecting', note: quick(it) ? `${cards.length ? cards.length + " 个结果，" : ""}有同款就点右边「有货源」` : `${cards.length} 个结果，请在卡片上点「选为货源」`, thumb: ti })
    await sendStatus(it.key, { status: 'selecting', note: it.note, thumb: ti })  // detail pages opened now belong to it
    call('POST', '/api/ext/claim', { items: [{ kind: it.kind, id: it.id }] }).catch(() => {})  // still working on it
    const zoom = document.getElementById('hy-zoom')
    if (zoom && it.image && zoom.dataset.src !== it.image) toggleZoom(it.image)  // large view follows the product being chosen
    let resolvePick
    const picked = new Promise((resolve) => { resolvePick = resolve })
    choose = resolvePick
    renderBatch(quick(it) ? `第 ${i + 1} 个：看一眼搜索结果，有同款就点插件里的「有货源」（不用点卡片，第二阶段再选具体货源），没有就点「跳过」（标记无货源，所有店铺库存清零）。`
      : `第 ${i + 1} 个${stage1() ? '（有曝光，优先：直接选货源）' : ''}：请在下方结果里点「选为货源」，不合适就点「跳过」。`, i)
    if (!quick(it) && !pickButtons(cards, (c) => resolvePick(c), it.best && it.best.offer_id)) resolvePick('nocards')
    const pick = await picked
    choose = null
    document.querySelectorAll('.hy-pick').forEach((x) => x.remove())
    if (pick === 'stop') Object.assign(it, prev.status === 'selecting' ? { status: 'waiting', note: '' } : prev)
    else if (pick === 'skip') {  // 无货源: stock 0 in every store it is listed in — not waited for: the next one opens now
      Object.assign(it, { status: 'nomatch', note: '跳过 · 正在标记无货源、清零库存…' })
      call('POST', '/api/ext/skip', { kind: it.kind, id: it.id }).then((r) => {
        const f = r.failed || []
        it.note = '跳过 · 无货源' + (r.zeroed ? `，${r.zeroed} 个店铺库存已清零` : '') +
          (r.not_listed ? `，${r.not_listed} 个店铺未上架（不用清零）` : '') +
          (!r.zeroed && !r.not_listed && !f.length ? '，店铺里没有库存要清零' : '') +
          (f.length ? `；${f.length} 个店铺清零失败：${f.map((x) => `${x.store} ${x.error || ''}`).join('；').slice(0, 120)}` : '')
      }, () => { it.note = '跳过（标记无货源失败，可点「重试」）'; it.status = 'error' }).then(() => {
        sendStatus(it.key, { status: it.status, note: it.note })
        if (!nextTimer) renderBatch(body().querySelector('#hy-msg') ? body().querySelector('#hy-msg').innerHTML : '',
          pageBatch.items.findIndex((x) => x.status === 'selecting'))
      })
    }
    else if (pick === 'found') {  // 第一阶段: 有货源 (no card picked) — the 货源 is chosen in 第二阶段
      Object.assign(it, { status: 'found', note: '有货源（第二阶段再精选）' })
      call('POST', '/api/ext/found', { kind: it.kind, id: it.id }).catch(() => { it.note = '有货源（保存失败，请重试）' })
    }
    else if (quick(it) && pick && pick.key) {  // 第一阶段: 有货源 — the best one is chosen in 第二阶段
      Object.assign(it, { status: 'found', note: `有货源 · ¥${pick.price ?? '-'}（第二阶段再精选）`, best: { offer_id: pick.key, price: pick.price, title: pick.title } })
      call('POST', '/api/ext/found', { kind: it.kind, id: it.id, offer_id: pick.key, price: pick.price ?? null, title: (pick.title || '').slice(0, 250),
        image_url: pick.image_url || '' }).catch(() => { it.note = '有货源（保存失败，请重试）' })
    }
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
  async function giveFiles(files, trigger, count = () => uploadedThumbs().length) {
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
    const before = count()
    for (let i = 0; i < 30 && !used && count() === before; i++) await sleep(300)  // the page may load its uploader first
    window.removeEventListener('message', onMsg)
    if (used) return used.n
    if (count() > before) return count() - before
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

  /* ---------- 一件代发 搜索页 (air.1688.com/app/channel-fe/search): 搜图片, one product at a time ----------
   * 「搜图片」 → 「点击上传」 the product's main image → 「确定」 → its results → you choose. */
  const isChannel = () => /channel-fe\/search/.test(location.href)
  function uploadPanel() {  // the 搜图片 upload box: around 「点击上传」, up to where its 「确定」 is
    const zone = byText(['点击上传'])
    if (!zone) return null
    let p = zone.parentElement
    for (let k = 0; p && k < 8; k++, p = p.parentElement) if (byText(['确定'], p)) return { zone, panel: p }
    return { zone, panel: zone.parentElement }
  }
  async function openImageSearch() {
    if (uploadPanel()) return uploadPanel()
    const type = byText(['搜关键词', '搜图片', '图片链接', '商品链接', '店铺链接'])  // the search type (dropdown)
    if (type) { realClick(type); await sleep(500) }
    const opt = [...document.querySelectorAll('li, div, span, a')].filter((e) => visible(e) && (e.textContent || '').replace(/\s+/g, '') === '搜图片')
      .sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0]  // the option in the list (below the box)
    if (opt) { realClick(opt); await sleep(600) }
    for (let i = 0; i < 6 && !uploadPanel(); i++) {
      const inp = [...document.querySelectorAll('input')].find((x) => visible(x) && /图片|粘贴/.test(x.placeholder || ''))
      if (inp) { inp.focus(); realClick(inp) }
      await sleep(500)
    }
    return uploadPanel()
  }
  const panelImgs = (panel) => [...panel.querySelectorAll('img')].filter((im) => visible(im) && im.getBoundingClientRect().width >= 30).length
  async function dropFile(zone, f) {  // last resort: drop the picture on the upload box
    const blob = await (await fetch(f.dataUrl)).blob()
    const dt = new DataTransfer()
    dt.items.add(new File([blob], f.name, { type: blob.type || 'image/jpeg' }))
    for (const t of ['dragenter', 'dragover', 'drop']) zone.dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true, dataTransfer: dt }))
  }
  /* after 确定 with several pictures: the row of uploaded pictures to switch between (reading order) */
  function channelThumbs(n) {
    const bar = byText(['综合排序'])
    const barTop = bar ? bar.getBoundingClientRect().top + window.scrollY : Infinity
    let best = null
    for (const box of document.querySelectorAll('div, ul')) {
      if (panel && panel.contains(box)) continue
      const imgs = [...box.children].map((c) => (c.tagName === 'IMG' ? c : c.querySelector('img'))).filter((im) => im && visible(im))
      if (imgs.length !== n) continue
      if (!imgs.every((im) => { const r = im.getBoundingClientRect(); return r.width >= 30 && r.width <= 160 && r.top + window.scrollY < barTop })) continue
      if (!best || box.querySelectorAll('*').length < best.box.querySelectorAll('*').length) best = { box, imgs }
    }
    if (!best) return []
    return best.imgs.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
      return Math.abs(ra.top - rb.top) > ra.height / 2 ? ra.top - rb.top : ra.left - rb.left
    })
  }
  /* 搜图片 with up to 10 pictures at once, then each picture's results in turn. false: do them one by one */
  async function runChannelMany(idx) {
    const list = idx.map((i) => pageBatch.items[i])
    renderBatch(`正在准备 ${list.length} 个商品主图…`)
    const imgs = await Promise.all(list.map((it) => send({ type: 'fetchImage', item: it }).catch(() => null)))
    if (imgs.some((x) => !x)) return false
    const up = await openImageSearch()
    if (!up) return false
    renderBatch(`正在上传 ${list.length} 个商品主图…`)
    const files = list.map((it, k) => ({ name: `${idx[k] + 1}-${it.kind}-${it.id}.jpg`, dataUrl: imgs[k] }))
    const before = panelImgs(up.panel)
    await giveFiles(files, up.zone, () => panelImgs(up.panel))
    for (let k = 0; k < 30 && panelImgs(up.panel) - before < files.length; k++) await sleep(500)
    if (panelImgs(up.panel) - before < files.length) {  // not all went in: start again one by one
      const cancel = byText(['取消'], up.panel)
      if (cancel) realClick(cancel)
      await sleep(500)
      return false
    }
    await sleep(800)
    const seq = groupSeq, sig = resultSig()
    const ok = byText(['确定'], up.panel) || byText(['确定'])
    if (ok) realClick(ok)
    for (let k = 0; k < 80 && groupSeq === seq && resultSig() === sig; k++) await sleep(250)
    await settle()
    let thumbs = []
    for (let k = 0; k < 20 && (thumbs = channelThumbs(files.length)).length !== files.length; k++) await sleep(250)
    if (thumbs.length !== files.length) return false  // no row of the uploaded pictures: one by one
    // results came in after 确定 (page data or the cards changed): they are the first picture's
    groupOwner = groupSeq !== seq || resultSig() !== sig ? 0 : -1
    for (let j = 0; j < list.length; j++) {
      if (stopping) return true
      const i = idx[j], it = list[j]
      Object.assign(it, { status: 'searching', note: '' })
      await sendStatus(it.key, { status: 'searching', note: '' })
      renderBatch(`正在打开第 ${j + 1}/${list.length} 个的搜索结果…`, i)
      thumbs = channelThumbs(files.length).length === files.length ? channelThumbs(files.length) : thumbs
      const group = await showPicture(j, thumbs[j])
      let cards = group && group.length ? collectCards(group) : []
      if (!cards.length && group && quick(it)) cards = collectCards()  // 第一阶段: only looked at, nothing to read
      else if (!cards.length && group) {
        for (let k = 0; k < 4 && resultImgs().length < 8; k++) { window.scrollBy(0, 400); await sleep(500) }
        cards = await scanCards()
        if (!cards.length) cards = collectCards()
      }
      if (!group) { Object.assign(it, { status: 'error', note: '点了这张图，搜索结果没有切换' }); await sendStatus(it.key, it); continue }
      await pickAmong(i, it, cards, j)
      await sleep(150)
    }
    return true
  }

  async function runOneChannel(i) {
    const it = pageBatch.items[i]
    await sendStatus(it.key, { status: 'searching', note: '' })
    Object.assign(it, { status: 'searching', note: '' })
    renderBatch(`第 ${i + 1} 个：正在上传主图搜图…`, i)
    let dataUrl = null
    try { dataUrl = await send({ type: 'fetchImage', item: it }) } catch (e) {
      Object.assign(it, { status: 'error', note: '主图读取失败' }); return sendStatus(it.key, it)
    }
    const up = await openImageSearch()
    if (!up) { Object.assign(it, { status: 'error', note: '没找到「搜图片」的上传框' }); return sendStatus(it.key, it) }
    const file = { name: `${i + 1}-${it.kind}-${it.id}.jpg`, dataUrl }
    const before = panelImgs(up.panel)
    let n = await giveFiles([file], up.zone, () => panelImgs(up.panel))
    if (!n && panelImgs(up.panel) <= before) { await dropFile(up.zone, file); for (let k = 0; k < 10 && panelImgs(up.panel) <= before; k++) await sleep(400) }
    if (!n && panelImgs(up.panel) <= before) {
      Object.assign(it, { status: 'error', note: '主图没能上传到「搜图片」' }); return sendStatus(it.key, it)
    }
    await sleep(800)
    const seq = groupSeq, sig = resultSig()
    const ok = byText(['确定'], up.panel) || byText(['确定'])
    if (ok) realClick(ok)
    for (let k = 0; k < 80 && groupSeq === seq && resultSig() === sig; k++) await sleep(250)  // the results come in
    await settle()
    let cards = groupSeq !== seq ? collectCards(lastGroup) : []
    if (!cards.length && quick(it)) cards = collectCards()  // 第一阶段: only looked at
    if (!cards.length && !quick(it)) {
      for (let k = 0; k < 4 && resultImgs().length < 8; k++) { window.scrollBy(0, 400); await sleep(500) }
      cards = await scanCards()
    }
    if (!cards.length && !quick(it)) cards = collectCards()
    return pickAmong(i, it, cards, null)
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

  /* 找货源 isolation: keep the claim on every product of the batch not finished yet (a claim lasts 2 hours);
   * one another account took meanwhile is left out */
  async function renewClaims() {
    if (!pageBatch) return
    const open = pageBatch.items.filter((it) => !['done', 'nomatch'].includes(it.status))
    if (!open.length) return
    try {
      const r = await call('POST', '/api/ext/claim', { items: open.map((it) => ({ kind: it.kind, id: it.id })) })
      for (const t of r.taken || []) {
        const it = pageBatch.items.find((x) => `${x.kind}:${x.id}` === t.key)
        if (it && it.status !== 'selecting') {
          Object.assign(it, { status: 'error', note: `已被账号「${t.by}」在找货源，已跳过` })
          await sendStatus(it.key, { status: it.status, note: it.note })
        }
      }
    } catch (e) { /* offline: try again later */ }
  }
  setInterval(() => { if (pageBatch) renewClaims() }, 10 * 60 * 1000)

  async function runBatchPage2(again) {
    const items = pageBatch.items
    await renewClaims()
    if (isChannel()) {  // 一件代发 搜索页: 搜图片 with all the pictures at once, else one by one
      const todo = items.map((it, i) => i).filter((i) => !(items[i].status === 'error' && /已被账号/.test(items[i].note || ''))).filter((i) => typeof again === 'number' ? i === again
        : !(items[i].status === 'done' || (again === 'failed' && !isFailed(items[i])) || (again === false && items[i].status !== 'waiting')))
      if (todo.length >= 2 && await runChannelMany(todo)) {
        if (stopping) return
        renderBatch('<span class="hy-msg">全部选完。点「打开货源」核对规格价格并设为货源；要换货源点「重选」。</span>')
        return setTimeout(maybeNextBatch, 0)
      }
      for (let i = 0; i < items.length; i++) {
        const it = items[i]
        if (stopping) return
        if (it.status === 'error' && /已被账号/.test(it.note || '')) continue
        if (typeof again === 'number' ? i !== again : (it.status === 'done' || (again === 'failed' && !isFailed(it)) || (again === false && it.status !== 'waiting'))) continue
        await runOneChannel(i)
        await sleep(150)
      }
      if (stopping) return
      renderBatch('<span class="hy-msg">全部选完。点「打开货源」核对规格价格并设为货源；要换货源点「重选」。</span>')
      return setTimeout(maybeNextBatch, 0)
    }
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
    setTimeout(maybeNextBatch, 0)
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
      if (ch.batch && ch.batch.newValue && ch.batch.newValue.started !== pageBatch.started) {
        // the next batch was given to this page: start it on a freshly loaded page (also if the tab was not reloaded)
        if (!running && finished()) { nextTimer = 'loading'; renderBatch('正在打开下一批…'); setTimeout(() => location.reload(), 300) }
        return
      }
      if (ch.batch && ch.batch.newValue) {
        pageBatch.items.forEach((it) => {
          const n = ch.batch.newValue.items.find((x) => x.key === it.key)
          if (n && n.status === 'done' && it.status !== 'done') {
            if (it.status === 'selecting' && choose) choose({ done: true, best: n.best, note: n.note })  // set on its detail page: next one
            else Object.assign(it, { status: 'done', note: n.note, best: n.best || it.best })
          }
        })
        if (!running && !nextTimer) renderBatch()
        maybeNextBatch()
      }
      return
    }
    if ((ch.current && !tabItem) || ch.token) refresh()  // a 批量 tab keeps its own product
  })
  refresh()
})()

/* 1688 货源助手 — runs on 1688 pages in the user's own (logged-in) browser.
 *  - search / image-search result pages: collect the offer cards, 「AI 核对」 sends them to the
 *    微信小店助手 server, which asks Gemini 同款 / 相似 / 不同; the 最优 card is highlighted.
 *  - offer detail pages: read the title, supplier, main image, every SKU price and the 运费, map the
 *    1688 SKUs to our specs, and 「设为货源」 saves it (written to 商品云库 and every store).
 * Nothing is clicked or submitted on 1688 itself: the page is only read. */
(function () {
  const P = window.HY1688
  const call = (method, path, body) => new Promise((resolve, reject) =>
    chrome.runtime.sendMessage({ type: 'api', method, path, body }, (r) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message))
      r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错'))
    }))
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
  const abs = (u) => { try { return new URL(u, location.href).href } catch (e) { return '' } }
  const bigImg = (u) => abs(String(u || '').replace(/_\d+x\d+(q\d+)?\.(jpg|png|webp).*$/i, '').replace(/\.(\d+x\d+)\./, '.'))

  let current = null
  let panel = null
  let quiet = false  // our own storage write: don't re-render

  function makePanel() {
    if (panel) return panel
    panel = document.createElement('div')
    panel.id = 'hy-panel'
    panel.innerHTML = '<header><span>1688 货源助手</span><span class="hy-toggle">—</span></header><div class="hy-body"></div>'
    panel.querySelector('header').onclick = () => panel.classList.toggle('min')
    document.body.appendChild(panel)
    return panel
  }
  const body = () => makePanel().querySelector('.hy-body')

  function curHtml() {
    if (!current) return '<p class="hy-muted">还没有选择商品：点浏览器右上角的插件图标，选一个要找货源的商品。</p>'
    return `<div class="hy-cur"><img src="${esc(current.image)}"><div style="flex:1;min-width:0">
      <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(current.title)}">${esc(current.title)}</div>
      <div class="hy-muted">${esc(current.store)} · ${current.specs.length} 个规格 · 售价 ¥${yuan(current.price_min)}</div></div></div>`
  }

  /* ---------- search result pages ---------- */
  function collectCards() {
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
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative'
    let b = el.querySelector(':scope > .hy-badge')
    if (!b) { b = document.createElement('span'); b.className = 'hy-badge'; el.appendChild(b) }
    b.className = 'hy-badge ' + cls
    b.textContent = text
    b.title = tip || ''
  }

  async function verifyCards() {
    const btn = panel.querySelector('#hy-verify'); const msg = panel.querySelector('#hy-msg')
    const cards = collectCards()
    if (!cards.length) { msg.innerHTML = '<span class="hy-err">这个页面上没找到商品卡片（可以先往下滚动，让图片加载出来）</span>'; return }
    document.querySelectorAll('.hy-best').forEach((x) => x.classList.remove('hy-best'))
    document.querySelectorAll('.hy-best-tag').forEach((x) => x.remove())
    const n = Math.min(cards.length, Number(panel.querySelector('#hy-n').value) || 20)
    const list = cards.slice(0, n)
    list.forEach((c) => badge(c, 'wait', 'AI 核对中…'))
    btn.disabled = true; msg.textContent = `正在核对前 ${list.length} 个商品…`
    try {
      const all = []
      for (let i = 0; i < list.length; i += 20) {
        const part = list.slice(i, i + 20)
        const data = await call('POST', '/api/ext/verify', { kind: current.kind, id: current.id,
          candidates: part.map((c) => ({ key: c.key, image_url: c.image_url, title: c.title })) })
        const by = Object.fromEntries(data.results.map((r) => [r.key, r]))
        for (const c of part) {
          const r = by[c.key] || { verdict: '未判断', confidence: 0, reason: '' }
          Object.assign(c, r)
          const cls = r.verdict === '同款' ? 'same' : r.verdict === '相似' ? 'similar' : 'diff'
          badge(c, cls, `${r.verdict} ${r.confidence || 0}%`, r.reason)
          all.push(c)
        }
      }
      const best = P.pickBest(all)
      if (best) {
        best.el.classList.add('hy-best')
        const t = document.createElement('span'); t.className = 'hy-best-tag'; t.textContent = '最优'; best.el.appendChild(t)
        best.el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
      const same = all.filter((c) => c.verdict === '同款').length
      const sim = all.filter((c) => c.verdict === '相似').length
      msg.innerHTML = `<span class="hy-msg">同款 ${same} 个，相似 ${sim} 个。</span>` +
        (best ? `最优：<a href="${esc(best.url)}" target="_blank">${esc(best.title.slice(0, 30))}</a>（¥${best.price ?? '-'}${best.sales ? '，已售 ' + best.sales : ''}）。打开详情页设为货源。` : '没有找到同款或相似的商品。')
    } catch (e) {
      list.forEach((c) => !c.verdict && badge(c, 'diff', '未核对'))
      msg.innerHTML = `<span class="hy-err">${esc(e.message)}</span>`
    } finally { btn.disabled = false }
  }

  function renderSearch() {
    body().innerHTML = curHtml() + (current ? `
      <div style="display:flex;gap:6px;align-items:center">
        <button id="hy-verify">AI 核对本页商品</button>
        <span class="hy-muted">前</span><input id="hy-n" type="number" value="20" min="1" max="60" style="width:56px">
        <span class="hy-muted">个</span></div>
      <p class="hy-muted">用 Gemini 比较主图和标题：绿色=同款，橙色=相似。最优 = 同款里销量最高的，没有同款就选最便宜的相似款。</p>
      <div id="hy-msg"></div>` : '')
    const b = panel.querySelector('#hy-verify'); if (b) b.onclick = verifyCards
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
    const opts = (sel) => o.skus.map((s, i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${esc(s.name)} ¥${s.price ?? '-'}</option>`).join('')
    const rows = current.specs.map((sp) => {
      const i = map[sp.key] ?? -1
      const p = i >= 0 ? o.skus[i].price : o.price
      return `<tr data-key="${esc(sp.key)}"><td style="width:34%">${esc(sp.label)}</td>
        <td>${o.skus.length ? `<select class="hy-sku"><option value="-1">（不对应）</option>${opts(i)}</select>` : ''}</td>
        <td style="width:70px"><input class="hy-price" type="number" step="0.01" min="0" value="${p ?? ''}"></td></tr>`
    }).join('')
    box.innerHTML = curHtml() + `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <img src="${esc(o.image_url)}" style="width:48px;height:48px;object-fit:cover;border-radius:4px">
        <div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.title)}</div>
        <div class="hy-muted">${esc(o.supplier)} · ${o.skus.length} 个 1688 规格</div></div></div>
      <div id="hy-ai" class="hy-muted"></div>
      <table><tr><th>我们的规格</th><th>1688 规格</th><th>货源价</th></tr>${rows}</table>
      <div style="display:flex;gap:6px;align-items:center"><span class="hy-muted" style="white-space:nowrap">运费 ¥</span>
        <input id="hy-freight" type="number" step="0.01" min="0" value="${o.freight ?? ''}" placeholder="没读到就手动填" style="width:90px">
        <span class="hy-muted">采购成本 = 货源价 + 运费</span></div>
      <div style="display:flex;gap:6px;margin-top:10px"><button id="hy-check">AI 核对</button><button class="hy-ok" id="hy-save">设为货源</button></div>
      <div id="hy-msg" style="margin-top:6px"></div>
      ${current.source && current.source.offer_id ? `<p class="hy-muted">当前货源：${esc(current.source.offer_id)}（¥${yuan(current.source.price)}），设为货源会替换它。</p>` : ''}`
    box.querySelectorAll('.hy-sku').forEach((s) => (s.onchange = () => {
      const sku = o.skus[Number(s.value)]
      if (sku && sku.price != null) s.closest('tr').querySelector('.hy-price').value = sku.price
    }))
    box.querySelector('#hy-check').onclick = async () => {
      const el = box.querySelector('#hy-ai'); el.textContent = 'AI 核对中…'
      try {
        const d = await call('POST', '/api/ext/verify', { kind: current.kind, id: current.id,
          candidates: [{ key: o.offer_id, image_url: o.image_url, title: o.title }] })
        const r = d.results[0] || {}
        const color = r.verdict === '同款' ? '#67c23a' : r.verdict === '相似' ? '#e6a23c' : '#f56c6c'
        el.innerHTML = `<b style="color:${color}">${esc(r.verdict)} ${r.confidence || 0}%</b> ${esc(r.reason || '')}`
      } catch (e) { el.innerHTML = `<span class="hy-err">${esc(e.message)}</span>` }
    }
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
        quiet = true
        await chrome.storage.local.set({ current })
        msg.innerHTML = '<span class="hy-msg">已设为货源，商品云库和各店铺里的同一商品已同步。</span>'
      } catch (e) { msg.innerHTML = `<span class="hy-err">${esc(e.message)}</span>` } finally { btn.disabled = false }
    }
  }

  /* ---------- start ---------- */
  const isDetail = () => /detail\.1688\.com\/offer\/|\/offer\/\d+\.html/.test(location.href)

  async function refresh() {
    const s = await chrome.storage.local.get(['current', 'token'])
    if (!s.token) { body().innerHTML = '<p class="hy-muted">请先点浏览器右上角的插件图标登录。</p>'; return }
    current = s.current || null
    if (current) {  // fresh specs + current 货源 from the server
      try { current = { ...current, ...(await call('GET', `/api/ext/item?kind=${current.kind}&id=${current.id}`)) } } catch (e) { /* keep cached */ }
    }
    if (isDetail()) await renderDetail(); else renderSearch()
    if (current && current.vision === false) {
      body().insertAdjacentHTML('afterbegin', '<p class="hy-err">网站还没有配置 Gemini API Key（系统设置 → 视觉 AI），AI 核对不可用。</p>')
    }
  }

  chrome.storage.onChanged.addListener((ch) => {
    if (quiet) { quiet = false; return }
    if (ch.current || ch.token) refresh()
  })
  refresh()
})()

/* 1688 货源助手 — on the 微信小店后台 商品列表 (store.weixin.qq.com/shop/goods/list).
 * 「读取本页曝光 / 读取全部页」 reads each product's ID and its 近30天 「曝光 N」 from the list (the API has no
 * such number) and saves them on the website; 找货源 then lists the products with the most 曝光 first.
 * Nothing on the page is changed (only 下一页 is clicked for 读取全部页). */
(function () {
  if (window.top !== window) return
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
  const onList = () => /\/shop\/goods\/list/.test(location.pathname + location.hash)

  /* the page may be built from web components: read through shadow roots too */
  const ours = (el) => !!(el && el.closest && el.closest('#hy-wx'))
  function deepText(root) {
    const out = []
    const walk = (node) => {
      if (node.nodeType === 3) { const t = node.nodeValue.trim(); if (t) out.push(t); return }
      if (node.nodeType === 1) {
        if (ours(node) || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(node.tagName)) return
        if (node.shadowRoot) walk(node.shadowRoot)
      } else if (node.nodeType !== 11) return
      for (const c of node.childNodes) walk(c)
    }
    walk(root)
    return out.join('\n')
  }
  function deepAll(root = document) {
    const out = []
    const walk = (r) => { for (const el of r.querySelectorAll('*')) { out.push(el); if (el.shadowRoot) walk(el.shadowRoot) } }
    walk(root)
    return out
  }
  const up = (el) => el.parentElement || ((el.getRootNode && el.getRootNode().host) || null)
  const toNum = (v, wan) => Math.round(parseFloat(String(v).replace(/,/g, '')) * (wan ? 10000 : 1)) || 0

  /* the rows of the list: {id, exposure, title} */
  function readRows() {
    const rows = new Map()
    for (const el of deepAll(document.body)) {
      if (ours(el)) continue
      // the element holding the 「ID:」 label (the number may sit in a child: ID: <span>1000…</span>)
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join('')
      if (!/ID\s*[:：]/.test(own)) continue
      const m = deepText(el).replace(/\n/g, ' ').match(/ID\s*[:：]\s*(\d{10,})/)
      if (!m || rows.has(m[1])) continue
      let row = el
      for (let k = 0; row && k < 12; k++) {  // up to the row: the one ID, with its 曝光
        const t = deepText(row)
        if ((t.match(/ID[:：]\s*\d{10,}/g) || []).length > 1) { row = null; break }
        if (/曝光/.test(t)) break
        row = up(row)
      }
      const t = row ? deepText(row) : ''
      const e = t.match(/曝光\s*[:：]?\s*([\d,.]+)\s*(万)?/)
      rows.set(m[1], { id: m[1], exposure: e ? toNum(e[1], e[2]) : 0, title: (t.split('\n').find((x) => x.length >= 4 && !/^ID/.test(x) && !/^[¥￥]/.test(x)) || '').slice(0, 40) })
    }
    return [...rows.values()]
  }

  function nextButton() {  // the pagination's 下一页 (not when disabled)
    const cls = (el) => (typeof el.className === 'string' ? el.className : '')
    const vis = deepAll(document.body).filter((el) => !ours(el) && el.getClientRects().length)
    let b = vis.filter((el) => (el.textContent || '').trim() === '下一页' || el.getAttribute('aria-label') === '下一页' ||
      el.getAttribute('title') === '下一页').pop()
    if (!b) b = vis.filter((el) => /(^|\s)([\w-]*(btn-next|pagination-next|nav_next|next-btn))(\s|$)/.test(cls(el))).pop()
    if (!b) return null
    const off = (x) => !!x && (x.disabled || x.getAttribute('aria-disabled') === 'true' || /disabled/.test(cls(x)))
    return off(b) || off(b.closest && b.closest('button, li, a')) ? null : b
  }

  /* ---------- the box (bottom left) ---------- */
  let box = null
  function render(html) {
    if (!box) {
      box = document.createElement('div')
      box.id = 'hy-wx'
      box.innerHTML = '<header><span>1688 货源助手 · 曝光</span><span>—</span></header><div class="b"></div>'
      box.querySelector('header').onclick = () => box.classList.toggle('min')
      box.addEventListener('click', (e) => {
        if (e.target.id === 'hy-wx-page') scan(false)
        if (e.target.id === 'hy-wx-all') scan(true)
        if (e.target.id === 'hy-wx-stop') stop = true
        if (e.target.id === 'hy-wx-go') startExposed()
      })
      document.body.appendChild(box)
    }
    box.querySelector('.b').innerHTML = html
  }
  const idle = (note) => render(`<div class="m">读取商品列表里每个商品的「曝光」（近30天），找货源时先找曝光多的商品。</div>
    ${note ? `<div>${note}</div>` : ''}
    <div><button id="hy-wx-page">读取本页曝光</button> <button id="hy-wx-all">读取全部页</button></div>`)

  let running = false, stop = false
  let needSource = []  // exposed products still without a 货源, most 曝光 first
  async function startExposed() {
    if (!needSource.length) return
    const b = document.getElementById('hy-wx-go'); if (b) { b.disabled = true; b.textContent = '正在打开 1688…' }
    try {
      const r = await send({ type: 'startBatch', items: needSource.slice(0, 10), query: { kind: 'store' } })
      if (b) b.textContent = `已打开 1688 找货源（${r.count} 个）${r.taken ? `，${r.taken} 个正在被其他账号找` : ''}`
    } catch (e) { if (b) { b.disabled = false; b.textContent = '给曝光商品找货源' } alert(e.message) }
  }
  async function scan(all) {
    if (running) return
    const { token } = await chrome.storage.local.get('token')
    if (!token) return idle('<span class="err">请先点浏览器右上角的插件图标登录</span>')
    running = true; stop = false
    const seen = new Map()
    let pages = 0
    try {
      for (;;) {
        let rows = readRows()
        for (let k = 0; k < 10 && !rows.length; k++) { await sleep(500); rows = readRows() }
        rows.forEach((r) => seen.set(r.id, r))
        pages++
        render(`正在读取第 ${pages} 页… 已读到 ${seen.size} 个商品<div><button id="hy-wx-stop">停止</button></div>`)
        if (!all || stop) break
        const nb = nextButton()
        if (!nb) break
        const first = rows[0] && rows[0].id
        nb.click()
        for (let k = 0; k < 30; k++) {  // the next page is shown
          await sleep(400)
          const now = readRows()
          if (now.length && now[0].id !== first) break
        }
        if (pages >= 200) break
      }
      const items = [...seen.values()]
      if (!items.length) return idle('<span class="err">这个页面上没读到商品（请在「商品列表」页面，等列表加载出来再点）</span>')
      const r = await call('POST', '/api/ext/exposure', { items: items.map((x) => ({ product_id: x.id, exposure: x.exposure })) })
      const top = items.filter((x) => x.exposure > 0).sort((a, b) => b.exposure - a.exposure).slice(0, 5)
      idle(`<span class="ok">已读取 ${pages} 页、${items.length} 个商品，${items.filter((x) => x.exposure > 0).length} 个有曝光；网站里对上 ${r.matched} 个</span>` +
        (r.unknown && r.unknown.length ? `<div class="m">${r.unknown.length} 个商品网站里还没有（请先在网站「商品管理」同步这个店铺）</div>` : '') +
        (top.length ? `<div class="m">曝光最多：${top.map((x) => `${esc(x.title || x.id)} ${x.exposure}`).join('；')}</div>` : '') +
        `<div>有曝光的 ${r.with_exposure} 个：已有货源 ${r.sourced || 0} 个${r.no_source ? `，无货源 ${r.no_source} 个` : ''}，<b>还要找货源 ${(r.need_source || []).length} 个</b></div>` +
        ((r.need_source || []).length ? `<div><button id="hy-wx-go">给曝光商品找货源（${Math.min(r.need_source.length, 10)}）</button></div>
          <div class="m">按曝光从多到少，一次 10 个，打开 1688 搜图找货源（和插件弹窗的「批量找货源」一样）。</div>` : '<div class="m">有曝光的商品都已经设好货源了。</div>'))
      needSource = r.need_source || []
    } catch (e) {
      idle(`<span class="err">${esc(e.message)}</span>`)
    } finally { running = false }
  }

  setInterval(() => {  // the 小店后台 is one page app: show the box on 商品列表 only
    if (onList()) { if (!box) idle('') } else if (box) { box.remove(); box = null }
  }, 1000)
})()

/* 1688 货源助手 — 采购跟踪 (runs on every 1688 page).
 * 1. 下单页「使用临时地址」: the buyer info copied in 订单管理 (复制收件信息 / 复制面单地址) is pasted
 *    → the website finds that order → it is remembered as「正在采购」.
 * 2. 点「立即下单 / 提交订单」: the price shown then (实付, incl. 运费) and each line's 单价 × 数量 are kept.
 * 3. 收银台: the 1688「订单编号」is read and saved with the price to the order's 采购 (采购单号 + 采购实付),
 *    and each spec's real 采购价 (the 1688 单价, as it is) is kept as its 货源价.
 * 4. 1688 订单详情 (from 订单管理「1688查看」): the 物流公司 + 运单号码 are read and the order is shipped.
 * storage.local.po = {order, at, placed: {cost, lines, at}, saved: [order numbers], msg} */
(function () {
  const send = (msg) => new Promise((resolve, reject) =>
    chrome.runtime.sendMessage(msg, (r) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message))
      r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错'))
    }))
  const call = (method, path, body) => send({ type: 'api', method, path, body })
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
  const KEEP = 3 * 3600 * 1000  // a 采购 not finished within 3 hours is forgotten
  const num = (s) => Number(String(s).replace(/,/g, ''))

  const getPo = async () => {
    const { po } = await chrome.storage.local.get('po')
    return po && po.order && Date.now() - po.at < KEEP ? po : null
  }
  const setPo = (po) => chrome.storage.local.set({ po })

  /* ---------- the box (bottom left) ---------- */
  const inFrame = window.top !== window
  let box = null
  const SWITCH = `<div class="sw" style="display:flex;gap:4px;margin-top:6px"><input id="hy-po-oid" placeholder="输入微信订单号，改为采购这个订单" style="flex:1;padding:2px 4px;border:1px solid #dcdfe6;border-radius:4px;font-size:12px"><button id="hy-po-sw" style="margin-top:0">换</button></div>`
  function render(po, note, sw) {
    if (inFrame) return  // the page itself shows the box
    if (!po && !note) { if (box) box.remove(); box = null; return }
    if (!box) {
      box = document.createElement('div')
      box.id = 'hy-po'
      box.innerHTML = '<header><span>1688 货源助手 · 采购跟踪</span><span>—</span></header><div class="b"></div>'
      box.querySelector('header').onclick = () => box.classList.toggle('min')
      box.addEventListener('click', (e) => {
        if (e.target.id === 'hy-po-clear') { setPo(null); render(null) }
        if (e.target.id === 'hy-po-close') render(null)
        if (e.target.id === 'hy-po-sw') switchOrder()
        if (e.target.id === 'hy-po-rec') recordPending()
      })
      box.addEventListener('keydown', (e) => { if (e.target.id === 'hy-po-oid' && e.key === 'Enter') switchOrder() })
      document.body.appendChild(box)
    }
    const b = box.querySelector('.b')
    if (!po) { b.innerHTML = `${note}${sw ? SWITCH : ''}<div><button id="hy-po-close">关闭</button></div>`; return }
    const o = po.order
    const items = (o.items || []).map((it) => `${esc(it.title)}${it.attrs ? '（' + esc(it.attrs) + '）' : ''} ×${it.qty}`).join('<br>')
    b.innerHTML = `<div>正在采购订单 <b>${esc(o.order_id)}</b></div>
      <div class="m">${esc(o.store)} · 收件人 ${esc(o.receiver_name)} · 买家实付 ¥${yuan(o.order_price)}</div>
      <div class="m">${items}</div>
      ${po.placed ? `<div>下单价 <b>¥${po.placed.cost != null ? po.placed.cost.toFixed(2) : '-'}</b>${(po.placed.lines || []).map((l) => ` · 单价 ¥${l.unit_price} ×${l.qty}`).join('')}</div>` : '<div class="m">点「立即下单」后记录下单价格，在收银台读取 1688 订单编号。</div>'}
      ${po.saved && po.saved.length ? `<div class="ok">已记录到订单的「采购」：</div><div class="no">${po.saved.map(esc).join('<br>')}</div>` : ''}
      ${note ? `<div>${note}</div>` : ''}
      ${pending.length ? `<div class="err">发现新的 1688 订单 <b>${pending.map(esc).join('、')}</b>：这个订单已经记录过采购单号，确认是同一个订单再记录，不是的话在下面输入正在采购的微信订单号。</div>
        <div><button id="hy-po-rec">记录到订单 ${esc(o.order_id)}</button></div>` : ''}
      ${SWITCH}
      <div><button id="hy-po-clear">${po.saved && po.saved.length ? '完成' : '不是这个订单'}</button></div>`
  }

  /* the order being bought, typed in (when the pasted address did not find it, or found the wrong one) */
  let pending = []  // 1688 order numbers seen on the 收银台, not recorded yet (the order already has one)
  async function switchOrder() {
    const inp = document.getElementById('hy-po-oid')
    const no = ((inp && inp.value) || '').replace(/\D/g, '')
    if (!no) return
    try {
      const r = await call('GET', '/api/ext/order?order_id=' + no)
      const old = await getPo()
      const po = { order: r.order, at: Date.now(), placed: (old && old.placed) || null, saved: [] }
      await setPo(po)
      curPo = po
      if (pending.length) return recordPending()  // on the 收银台: record the new 1688 order to the chosen one
      render(po, '<span class="ok">已改为采购这个订单</span>')
    } catch (err) { render(await getPo(), `<span class="err">${esc(err.message)}</span>`, true) }
  }
  async function recordPending() {
    const po = await getPo()
    if (!po || !pending.length) return
    const nos = pending
    pending = []
    await record(po, nos)
  }

  /* ---------- 1. pasted buyer info ---------- */
  document.addEventListener('paste', async (e) => {
    const t = e.target
    if (!t || !(t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable)) return
    const text = (e.clipboardData && e.clipboardData.getData('text')) || ''
    const flat = text.replace(/[\s-]/g, '')
    if (text.length < 10 || text.length > 1000 || !/\d{7,}/.test(flat) || !/[省市区县镇]/.test(text)) return  // not name + phone + address
    const { token } = await chrome.storage.local.get('token')
    if (!token) return
    try {
      const r = await call('POST', '/api/ext/order-match', { text })
      if (!r.order) return render(await getPo(), '<span class="err">粘贴的收件信息没有对应到订单（请从订单管理里「复制」收件信息）</span>')
      const po = { order: r.order, at: Date.now(), placed: null, saved: [] }
      await setPo(po)
      render(po, r.order.purchase_order_no ? `<span class="err">这个订单已经有采购单号 ${esc(r.order.purchase_order_no)}，再下单会追加</span>` : '<span class="ok">已识别订单，下单后自动记录采购单号和实付</span>')
    } catch (err) { render(await getPo(), `<span class="err">${esc(err.message)}</span>`) }
  }, true)

  /* the page's own text, without our boxes (their 「买家实付 ¥13.96」 is not a 1688 price) */
  const ours = (el) => !!(el && el.closest && el.closest('#hy-po, #hy-panel, #hy-zoom'))
  function pageText() {  // shadow roots included (air.1688.com pages are web components), our boxes left out
    return document.body ? deepText(document.body) : ''
  }
  const up = (el) => el.parentElement || ((el.getRootNode && el.getRootNode().host) || null)  // across shadow roots
  const MONEY = /(\d[\d,]*\.\d{1,2})/  // 1688 often draws the ¥ with CSS: only the number is text
  const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))

  /* ---------- 2. 下单页: the price at the time of the order ---------- */
  let curPo = null  // kept in memory: the 立即下单 click must be recorded before the page goes to the 收银台
  getPo().then((po) => { curPo = po })
  function orderButton() {  // 立即下单 / 提交订单: the element around the text, up to where the total is shown
    let found = null
    for (const el of deepAll(document.body)) {
      if (ours(el) || !visible(el)) continue
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join('').trim()
      if (/^(立即下单|提交订单)/.test(own)) { found = el; break }
    }
    if (!found) return null
    let btn = found
    for (let k = 0, p = up(found); p && k < 3; k++, p = up(p)) {  // the button itself: its text starts with 立即下单
      const t = deepText(p)
      if (!/^(立即下单|提交订单)/.test(t) || t.length >= 40) break
      btn = p
    }
    return btn
  }
  function moneyIn(root) {  // the prices shown in this part of the page, in order (each its own element: ¥2.00, 2.00 元)
    const out = []
    for (const el of deepAll(root)) {
      if (el.children.length || el.shadowRoot || ours(el)) continue
      const m = (el.textContent || '').trim().match(/^[¥￥]?\s*(\d[\d,]*\.\d{1,2})\s*(元.*)?$/)
      if (m) out.push(num(m[1]))
    }
    return out
  }
  function orderLines() {  // [{offer_id, unit_price, qty}] from the 下单 page
    const lines = []
    const urlOffer = (location.href.match(/offer[_-]?id=(\d{6,})/i) || [])[1] || ''
    const qtyInputs = deepAll(document.body).filter((i) => i.tagName === 'INPUT' && !ours(i) && /^\d{1,6}$/.test(i.value || '') &&
      i.offsetWidth && i.offsetWidth < 120)
    for (const inp of qtyInputs) {
      let row = up(inp)
      for (let k = 0; row && k < 8 && !MONEY.test(deepText(row)); k++) row = up(row)
      if (!row) continue
      const prices = moneyIn(row)
      if (!prices.length) continue
      let wrap = row, ids = new Set()
      for (let k = 0; wrap && k < 6; k++, wrap = up(wrap)) {
        ids = new Set(deepAll(wrap).filter((a) => a.tagName === 'A' && /\/offer\//.test(a.href || '')).map((a) => (a.href.match(/\/offer\/(\d+)/) || [])[1]).filter(Boolean))
        if (ids.size) break
      }
      lines.push({ offer_id: ids.size === 1 ? [...ids][0] : (urlOffer || ''), unit_price: prices[0], qty: Number(inp.value) || 1 })
    }
    return lines.filter((l) => l.unit_price > 0).slice(0, 50)
  }
  function orderTotal(btn) {
    const own = deepText(btn).match(MONEY)
    if (own) return num(own[1])
    const page = pageText().match(/(?:实付款|应付总额|应付|合计|总计)[^\d]{0,16}(\d[\d,]*\.\d{1,2})/)
    return page ? num(page[1]) : null
  }
  let lastSnap = ''
  function snapshot() {  // what the 下单 page shows now; kept up to date while it is open
    if (!curPo || curPo.saved && curPo.saved.length) return
    const btn = orderButton()
    if (!btn) return
    const placed = { cost: orderTotal(btn), lines: orderLines(), at: Date.now() }
    const key = JSON.stringify([placed.cost, placed.lines])
    if (key === lastSnap || placed.cost == null) return
    lastSnap = key
    curPo = { ...curPo, placed }
    setPo(curPo)
    render(curPo)
  }
  document.addEventListener('click', (e) => {  // 立即下单: record now (no waiting: the page is leaving)
    if (!curPo || !e.target || ours(e.target)) return
    const path = (e.composedPath ? e.composedPath() : [e.target]).filter((x) => x && x.nodeType === 1).slice(0, 6)
    if (path.some((el) => { const t = deepText(el); return /^\s*(立即下单|提交订单)/.test(t) && t.length < 40 })) { lastSnap = ''; snapshot() }
  }, true)

  /* ---------- 3. 收银台: the 1688 订单编号 and the amount to pay ---------- */
  function orderNumbers() {
    const nos = [...pageText().matchAll(/订单(?:编号|号)\s*[:：]?\s*(\d{12,24})/g)].map((m) => m[1])
    const u = location.href.match(/(?:orderId|order_id|orderIds)=([\d,%2C]{12,})/i)
    if (u) nos.push(...decodeURIComponent(u[1]).split(',').filter((x) => /^\d{12,24}$/.test(x)))
    return [...new Set(nos)]
  }
  function cashierAmount() {  // the big price on the 收银台 (e.g. ¥7.30): the largest money text on the page
    let best = null, size = 0
    for (const el of deepAll(document.body)) {
      if (el.children.length > 2 || ours(el) || !visible(el)) continue
      const t = (el.textContent || '').trim()
      const m = t.match(/^[¥￥]?\s*(\d[\d,]*\.\d{2})$/)
      if (!m) continue
      const fs = parseFloat(getComputedStyle(el).fontSize) || 0
      if (fs > size) { size = fs; best = num(m[1]) }
    }
    return best
  }
  const onCashier = () => /cashier|收银台/.test(location.href) || /收银台/.test(document.title || '') ||
    /1688\s*收银台|支付结算币种|请在[\s\S]{0,20}内完成支付/.test(pageText())
  let saving = false
  async function check() {
    const po = await getPo()
    curPo = po
    if (!po) return offerSwitch()
    if (!onCashier()) return snapshot()
    if (saving) return
    const nos = orderNumbers().filter((n) => !(po.saved || []).includes(n))
    if (!nos.length) return
    if ((po.saved || []).length) {  // this order has its 1688 order already: ask before adding another
      if (nos.join() !== pending.join()) { pending = nos; render(po) }
      return
    }
    await record(po, nos)
  }
  let offered = false
  function offerSwitch() {  // the 下单 page / 收银台 without an order being followed: it can be typed in
    if (offered || box || isOrderDetail()) return
    const cashier = onCashier()
    if (!cashier && !orderButton()) return
    offered = true
    if (cashier) pending = orderNumbers()  // typed in here: these get recorded to it
    render(null, '采购跟踪：还没有对应的微信订单。在「使用临时地址」粘贴订单管理里复制的收件信息，或在下面输入正在采购的微信订单号。', true)
  }
  async function record(po, nos) {
    saving = true
    try {
      const pay = cashierAmount()  // what is really paid (incl. 运费)
      const cost = pay != null ? pay : (po.placed ? po.placed.cost : null)
      const r = await call('POST', '/api/ext/purchase', { id: po.order.id, purchase_order_nos: nos, purchase_cost: cost,
        lines: (po.placed && po.placed.lines) || [] })
      po.saved = [...(po.saved || []), ...nos]
      po.order = r.order || po.order
      if (cost != null) po.placed = { ...(po.placed || { lines: [] }), cost }
      await setPo(po)
      curPo = po
      render(po, (cost == null ? '<span class="err">没有读到采购金额，请在订单的「采购」里填写采购实付</span><br>' : '') +
        (r.prices_changed ? `<span class="ok">已记录 ${r.prices_changed} 个规格的真实采购价</span>` : ''))
    } catch (err) {
      render(po, `<span class="err">记录采购单号失败：${esc(err.message)}</span>`)
    } finally { saving = false }
  }

  /* ---------- 4. 1688 订单详情: 物流公司 + 运单号码 → 微信小店 发货 ---------- */
  // The 物流信息 may load in a frame of its own: a frame only reads it and hands it to the page.
  const isOrderDetail = () => !inFrame && (/[?&]orderId=\d{10,}/i.test(location.href) ||
    /order.*detail|detail.*order/i.test(location.pathname))
  function logistics() {
    const t = deepText(document.body)
    const waybill = (t.match(/运单号(?:码)?\s*[:：]\s*([A-Za-z0-9-]{6,})/) || [])[1]
    const company = (t.match(/物流公司\s*[:：]\s*([^\s\n]+)/) || [])[1]
    return waybill && company ? { waybill, company } : null
  }
  /* air.1688.com pages are built from web components: their text and tabs sit in shadow roots, which
     innerText / querySelectorAll do not see. Walk them too. */
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
  function deepAll(root = document) {  // every element, shadow roots included
    const out = []
    const walk = (r) => {
      for (const el of r.querySelectorAll('*')) {
        out.push(el)
        if (el.shadowRoot) walk(el.shadowRoot)
      }
    }
    walk(root)
    return out
  }
  let frameLg = null
  window.addEventListener('message', (e) => {
    if (e.data && e.data.hy1688lg && e.data.hy1688lg.waybill) frameLg = e.data.hy1688lg
  })
  function clickTab(text) {  // the innermost visible element showing exactly this text (shadow roots too)
    const els = deepAll().filter((x) => !ours(x) && x.getClientRects().length && deepText(x).trim() === text)
    const el = els[els.length - 1]
    if (!el) return false
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, composed: true, cancelable: true, view: window }))
    return true
  }
  async function shipIntent() {  // opened from 订单管理「1688查看」: which order to ship
    const h = Number((location.hash.match(/hyship=(\d+)/) || [])[1])
    if (h) return h
    const t = await send({ type: 'myShip' }).catch(() => null)  // the link's marker, kept by the plugin (1688 may redirect)
    return (t && t.id) || null
  }
  async function autoShip() {
    if (!isOrderDetail()) return
    const id = await shipIntent()
    const { token } = await chrome.storage.local.get('token')
    if (!token) { if (id) render(null, '<span class="err">请先点浏览器右上角的插件图标登录，再点一次「1688查看」</span>'); return }
    const poNo = () => (location.href.match(/orderId=(\d{10,})/i) || [])[1] || (deepText(document.body).match(/订单号\s*[:：]\s*(\d{12,24})/) || [])[1] || ''
    const ship = async (lg) => {
      render(null, `正在发货：${esc(lg.company)} ${esc(lg.waybill)}…`)
      try {
        const r = await call('POST', '/api/ext/ship', { id, purchase_order_no: poNo(), company: lg.company, waybill_id: lg.waybill })
        render(null, r.duplicate ? `<span class="ok">订单 ${esc(r.order_id)} 已经用这个运单号发过货了</span>`
          : `<span class="ok">已发货：订单 ${esc(r.order_id)}</span><div>${esc(lg.company)} · <b>${esc(lg.waybill)}</b></div>`)
      } catch (err) { manual(lg, `<span class="err">发货失败：${esc(err.message)}</span>`) }
    }
    const manual = (lg, note) => {  // not read (or not shipped): type / correct it here
      render(null, `${note}<div class="m" style="margin-top:6px">物流公司</div><input id="hy-lg-c" value="${esc(lg ? lg.company : '')}" placeholder="如 申通快递" style="width:100%">
        <div class="m">运单号码</div><input id="hy-lg-w" value="${esc(lg ? lg.waybill : '')}" placeholder="1688 物流信息里的运单号码" style="width:100%">
        <div><button id="hy-lg-go">发货到微信小店订单</button> <button id="hy-lg-again">重新读取</button></div>`)
      document.getElementById('hy-lg-go').onclick = () => {
        const c = document.getElementById('hy-lg-c').value.trim(), w = document.getElementById('hy-lg-w').value.trim()
        if (c && w) ship({ company: c, waybill: w })
      }
      document.getElementById('hy-lg-again').onclick = () => read(true)
    }
    const read = async (auto) => {
      if (id || auto) render(null, '正在读取 1688 物流信息…')
      let lg = null
      for (let i = 0; i < 30 && !lg; i++) {  // the 物流信息 tab fills in after a click
        lg = logistics() || frameLg
        if (!lg && (i === 1 || i === 6 || i === 15)) clickTab('物流信息')
        if (!lg) await new Promise((r) => setTimeout(r, 700))
      }
      if (!lg) {
        if (id || auto) manual(null, '<span class="err">没有读到物流公司和运单号码（卖家可能还没发货）。可以点「物流信息」看一下，手动填写后发货。</span>')
        return
      }
      if (id) return ship(lg)  // opened from 订单管理: ship right away
      render(null, `1688 物流：${esc(lg.company)} · <b>${esc(lg.waybill)}</b><div><button id="hy-po-ship">发货到微信小店订单</button></div>`)
      const b = document.getElementById('hy-po-ship')
      if (b) b.onclick = () => ship(lg)
    }
    read(false)
  }
  if (inFrame) {  // a frame of the order page: report the 物流 when it shows up
    let n = 0
    const t = setInterval(() => {
      const lg = logistics()
      if (lg) { window.top.postMessage({ hy1688lg: lg }, '*'); clearInterval(t) }
      if (++n > 60) clearInterval(t)
    }, 1000)
  }

  getPo().then((po) => { if (po && !isOrderDetail()) render(po) })
  autoShip()
  let tries = 0
  if (!inFrame) setInterval(() => { if (++tries < 3600) check() }, 1500)  // pages fill in late; the 下单 page is kept up to date
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.po) { const po = ch.po.newValue; curPo = po && po.order ? po : null; if (isOrderDetail()) return; if (curPo) render(curPo); else if (box) render(null) }
  })
})()

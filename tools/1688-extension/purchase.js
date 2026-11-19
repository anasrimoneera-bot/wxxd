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
  let box = null
  function render(po, note) {
    if (!po && !note) { if (box) box.remove(); box = null; return }
    if (!box) {
      box = document.createElement('div')
      box.id = 'hy-po'
      box.innerHTML = '<header><span>1688 货源助手 · 采购跟踪</span><span>—</span></header><div class="b"></div>'
      box.querySelector('header').onclick = () => box.classList.toggle('min')
      box.addEventListener('click', (e) => {
        if (e.target.id === 'hy-po-clear') { setPo(null); render(null) }
        if (e.target.id === 'hy-po-close') render(null)
      })
      document.body.appendChild(box)
    }
    const b = box.querySelector('.b')
    if (!po) { b.innerHTML = `${note}<div><button id="hy-po-close">关闭</button></div>`; return }
    const o = po.order
    const items = (o.items || []).map((it) => `${esc(it.title)}${it.attrs ? '（' + esc(it.attrs) + '）' : ''} ×${it.qty}`).join('<br>')
    b.innerHTML = `<div>正在采购订单 <b>${esc(o.order_id)}</b></div>
      <div class="m">${esc(o.store)} · 收件人 ${esc(o.receiver_name)} · 买家实付 ¥${yuan(o.order_price)}</div>
      <div class="m">${items}</div>
      ${po.placed ? `<div>下单价 <b>¥${po.placed.cost != null ? po.placed.cost.toFixed(2) : '-'}</b>${(po.placed.lines || []).map((l) => ` · 单价 ¥${l.unit_price} ×${l.qty}`).join('')}</div>` : '<div class="m">点「立即下单」后记录下单价格，在收银台读取 1688 订单编号。</div>'}
      ${po.saved && po.saved.length ? `<div class="ok">已记录到订单的「采购」：</div><div class="no">${po.saved.map(esc).join('<br>')}</div>` : ''}
      ${note ? `<div>${note}</div>` : ''}
      <div><button id="hy-po-clear">${po.saved && po.saved.length ? '完成' : '不是这个订单'}</button></div>`
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
  function pageText() {
    let t = document.body ? document.body.innerText || '' : ''
    for (const id of ['hy-po', 'hy-panel', 'hy-zoom']) {
      const el = document.getElementById(id)
      if (el && el.innerText) t = t.split(el.innerText).join('')
    }
    return t
  }
  const MONEY = /(\d[\d,]*\.\d{1,2})/  // 1688 often draws the ¥ with CSS: only the number is text
  const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length))

  /* ---------- 2. 下单页: the price at the time of the order ---------- */
  let curPo = null  // kept in memory: the 立即下单 click must be recorded before the page goes to the 收银台
  getPo().then((po) => { curPo = po })
  function orderButton() {  // 立即下单 / 提交订单 (with the total on it)
    const els = [...document.querySelectorAll('button, a, div, span')].filter((el) => !ours(el) && visible(el) &&
      /^\s*(立即下单|提交订单)/.test(el.innerText || '') && (el.innerText || '').length < 40)
    return els.length ? els[els.length - 1] : null  // the innermost one
  }
  function moneyIn(root) {  // the prices shown in this part of the page, in order (each its own element: ¥2.00, 2.00 元)
    const out = []
    for (const el of root.querySelectorAll('*')) {
      if (el.children.length || ours(el)) continue
      const m = (el.textContent || '').trim().match(/^[¥￥]?\s*(\d[\d,]*\.\d{1,2})\s*(元.*)?$/)
      if (m) out.push(num(m[1]))
    }
    return out
  }
  function orderLines() {  // [{offer_id, unit_price, qty}] from the 下单 page
    const lines = []
    const urlOffer = (location.href.match(/offer[_-]?id=(\d{6,})/i) || [])[1] || ''
    const qtyInputs = [...document.querySelectorAll('input')].filter((i) => !ours(i) && /^\d{1,6}$/.test(i.value || '') &&
      i.offsetWidth && i.offsetWidth < 120)
    for (const inp of qtyInputs) {
      let row = inp.parentElement
      for (let k = 0; row && k < 8 && !MONEY.test(row.innerText || ''); k++) row = row.parentElement
      if (!row) continue
      const prices = moneyIn(row)
      if (!prices.length) continue
      let wrap = row, ids = new Set()
      for (let k = 0; wrap && k < 6; k++, wrap = wrap.parentElement) {
        ids = new Set([...wrap.querySelectorAll('a[href*="/offer/"]')].map((a) => (a.href.match(/\/offer\/(\d+)/) || [])[1]).filter(Boolean))
        if (ids.size) break
      }
      lines.push({ offer_id: ids.size === 1 ? [...ids][0] : (urlOffer || ''), unit_price: prices[0], qty: Number(inp.value) || 1 })
    }
    return lines.filter((l) => l.unit_price > 0).slice(0, 50)
  }
  function orderTotal(btn) {
    const own = (btn.innerText || '').match(MONEY)
    if (own) return num(own[1])
    const page = pageText().match(/(?:实付款|应付总额|应付|合计|总计)[^\d\n]{0,12}(\d[\d,]*\.\d{1,2})/)
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
    const t = e.target
    if (!curPo || !t || ours(t)) return
    let el = t
    for (let k = 0; el && k < 5 && !/^\s*(立即下单|提交订单)/.test(el.innerText || ''); k++) el = el.parentElement
    if (el && /^\s*(立即下单|提交订单)/.test(el.innerText || '') && (el.innerText || '').length < 40) { lastSnap = ''; snapshot() }
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
    for (const el of document.body.querySelectorAll('*')) {
      if (el.children.length > 2 || ours(el) || !visible(el)) continue
      const t = (el.innerText || '').trim()
      const m = t.match(/^[¥￥]?\s*(\d[\d,]*\.\d{2})$/)
      if (!m) continue
      const fs = parseFloat(getComputedStyle(el).fontSize) || 0
      if (fs > size) { size = fs; best = num(m[1]) }
    }
    return best
  }
  const onCashier = () => /cashier|收银台/.test(location.href) || /收银台/.test(document.title || '') ||
    /1688\s*收银台|支付结算币种|请在.{0,12}内完成支付/.test(pageText())
  let saving = false
  async function check() {
    const po = await getPo()
    curPo = po
    if (!po) return
    if (!onCashier()) return snapshot()
    if (saving) return
    const nos = orderNumbers().filter((n) => !(po.saved || []).includes(n))
    if (!nos.length) return
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
  const isOrderDetail = () => /trade\.1688\.com\/order\/.*(detail|order_detail)/i.test(location.href)
  const shipFor = () => Number((location.hash.match(/hyship=(\d+)/) || [])[1]) || null  // opened from 订单管理「1688查看」
  function logistics() {
    const t = pageText()
    const waybill = (t.match(/运单号码\s*[:：]\s*([A-Za-z0-9-]{6,})/) || [])[1]
    const company = (t.match(/物流公司\s*[:：]\s*([^\s\n]+)/) || [])[1]
    return waybill && company ? { waybill, company } : null
  }
  function clickTab(text) {
    const el = [...document.querySelectorAll('a, li, div, span')].find((x) => !ours(x) && visible(x) &&
      (x.innerText || '').trim() === text && x.children.length <= 1)
    if (el) el.click()
    return !!el
  }
  async function autoShip() {
    if (!isOrderDetail()) return
    const { token } = await chrome.storage.local.get('token')
    if (!token) return
    const poNo = (location.href.match(/orderId=(\d{10,})/i) || [])[1] || (pageText().match(/订单号\s*[:：]\s*(\d{12,24})/) || [])[1] || ''
    const id = shipFor()
    let lg = null
    for (let i = 0; i < 20 && !lg; i++) {  // the 物流信息 tab fills in after a click
      lg = logistics()
      if (!lg && (i === 2 || i === 8)) clickTab('物流信息')
      if (!lg) await new Promise((r) => setTimeout(r, 700))
    }
    if (!lg) {
      if (id) render(null, '<span class="err">这个 1688 订单还没有物流信息（卖家可能还没发货），发货后再点「1688查看」</span>')
      return
    }
    const go = async () => {
      render(null, `正在发货：${esc(lg.company)} ${esc(lg.waybill)}…`)
      try {
        const r = await call('POST', '/api/ext/ship', { id, purchase_order_no: poNo, company: lg.company, waybill_id: lg.waybill })
        render(null, r.duplicate ? `<span class="ok">订单 ${esc(r.order_id)} 已经用这个运单号发过货了</span>`
          : `<span class="ok">已发货：订单 ${esc(r.order_id)}</span><div>${esc(lg.company)} · <b>${esc(lg.waybill)}</b></div>`)
      } catch (err) { render(null, `<span class="err">发货失败：${esc(err.message)}</span><div class="m">${esc(lg.company)} ${esc(lg.waybill)}</div>`) }
    }
    if (id) return go()  // opened from 订单管理: ship right away
    render(null, `1688 物流：${esc(lg.company)} · <b>${esc(lg.waybill)}</b><div><button id="hy-po-ship">发货到微信小店订单</button></div>`)
    const b = document.getElementById('hy-po-ship')
    if (b) b.onclick = go
  }

  getPo().then((po) => { if (po && !isOrderDetail()) render(po) })
  autoShip()
  let tries = 0
  setInterval(() => { if (++tries < 3600) check() }, 1500)  // pages fill in late; the 下单 page is kept up to date
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.po) { const po = ch.po.newValue; curPo = po && po.order ? po : null; if (isOrderDetail()) return; if (curPo) render(curPo); else if (box) render(null) }
  })
})()

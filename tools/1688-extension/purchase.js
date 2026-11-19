/* 1688 货源助手 — 采购跟踪 (runs on every 1688 page).
 * 1. 下单页「使用临时地址」: the buyer info copied in 订单管理 (复制收件信息 / 复制面单地址) is pasted
 *    → the website finds that order → it is remembered as「正在采购」.
 * 2. 点「立即下单 / 提交订单」: the price shown then (实付, incl. 运费) and each line's 单价 × 数量 are kept.
 * 3. 收银台: the 1688「订单编号」is read and saved with the price to the order's 采购 (采购单号 + 采购实付),
 *    and each spec's real 采购价 (the 1688 单价, as it is) is kept as its 货源价.
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

  /* ---------- 2. 立即下单: the price at the time of the order ---------- */
  function orderLines() {  // [{offer_id, unit_price, qty}] from the 下单 page
    const lines = []
    const urlOffer = (location.href.match(/offer[_-]?id=(\d{6,})/i) || [])[1] || ''
    const qtyInputs = [...document.querySelectorAll('input')].filter((i) => /^\d{1,6}$/.test(i.value || '') && i.offsetWidth && i.offsetWidth < 120)
    for (const inp of qtyInputs) {
      let row = inp.parentElement
      for (let k = 0; row && k < 8 && !/¥\s*[\d,.]+/.test(row.innerText || ''); k++) row = row.parentElement
      if (!row) continue
      const prices = [...(row.innerText || '').matchAll(/¥\s*([\d,]+(?:\.\d+)?)/g)].map((m) => num(m[1]))
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
  function totalNear(el) {
    const own = (el.innerText || '').match(/¥\s*([\d,]+(?:\.\d+)?)/)
    if (own) return num(own[1])
    const page = (document.body.innerText || '').match(/(?:实付款|应付总额|合计|总计)[^¥]{0,12}¥\s*([\d,]+(?:\.\d+)?)/)
    return page ? num(page[1]) : null
  }
  document.addEventListener('click', async (e) => {
    const el = e.target && e.target.closest && e.target.closest('button, a, div, span')
    if (!el) return
    let btn = el
    for (let k = 0; btn && k < 4 && !/^\s*(立即下单|提交订单)/.test(btn.innerText || ''); k++) btn = btn.parentElement
    if (!btn || !/^\s*(立即下单|提交订单)/.test(btn.innerText || '') || (btn.innerText || '').length > 40) return
    const po = await getPo()
    if (!po) return
    po.placed = { cost: totalNear(btn), lines: orderLines(), at: Date.now() }
    await setPo(po)
    render(po)
  }, true)

  /* ---------- 3. 收银台: the 1688 订单编号 ---------- */
  function orderNumbers() {
    const text = document.body ? document.body.innerText || '' : ''
    const nos = [...text.matchAll(/订单(?:编号|号)\s*[:：]?\s*(\d{12,24})/g)].map((m) => m[1])
    const u = location.href.match(/(?:orderId|order_id|orderIds)=([\d,%2C]{12,})/i)
    if (u) nos.push(...decodeURIComponent(u[1]).split(',').filter((x) => /^\d{12,24}$/.test(x)))
    return [...new Set(nos)]
  }
  const onCashier = () => /cashier|收银台/.test(location.href) || /收银台/.test(document.title || '') ||
    !!(document.body && /1688\s*收银台|支付结算币种|请在.{0,12}内完成支付/.test(document.body.innerText || ''))
  let saving = false
  async function check() {
    const po = await getPo()
    if (!po || saving || !onCashier()) return
    const nos = orderNumbers().filter((n) => !(po.saved || []).includes(n))
    if (!nos.length) return
    saving = true
    try {
      let cost = po.placed ? po.placed.cost : null
      if (cost == null) {  // not seen on the 下单 page: the amount to pay on the 收银台
        const m = (document.body.innerText || '').match(/¥\s*([\d,]+\.\d{2})/)
        cost = m ? num(m[1]) : null
      }
      const r = await call('POST', '/api/ext/purchase', { id: po.order.id, purchase_order_nos: nos, purchase_cost: cost,
        lines: (po.placed && po.placed.lines) || [] })
      po.saved = [...(po.saved || []), ...nos]
      po.order = r.order || po.order
      await setPo(po)
      render(po, r.prices_changed ? `<span class="ok">已记录 ${r.prices_changed} 个规格的真实采购价</span>` : '')
    } catch (err) {
      render(po, `<span class="err">记录采购单号失败：${esc(err.message)}</span>`)
    } finally { saving = false }
  }

  getPo().then((po) => { if (po) render(po) })
  let tries = 0
  const timer = setInterval(() => { check(); if (++tries > 120) clearInterval(timer) }, 1000)  // pages fill in late
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.po) { const po = ch.po.newValue; if (po && po.order) render(po); else if (box && !ch.po.newValue) render(null) }
  })
})()

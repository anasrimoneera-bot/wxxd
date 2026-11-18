/* 1688 货源助手 — popup: website address + login, picking products (up to 10) and the batch progress. */
const $ = (id) => document.getElementById(id)
const send = (msg) => new Promise((resolve, reject) =>
  chrome.runtime.sendMessage(msg, (r) => (r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错')))))
const call = (method, path, body) => send({ type: 'api', method, path, body })
const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
const MAX = 10
const STATUS = { waiting: '等待打开', searching: '上传主图中', uploaded: '搜索中', confirm: '待确认', done: '已设货源', nomatch: '没找到同款', error: '出错' }
let items = []
const picked = new Map()

async function init() {
  const s = await chrome.storage.local.get(['server', 'token', 'user', 'current'])
  $('server').value = s.server || ''
  const on = !!s.token
  $('login').hidden = on; $('main').hidden = !on; $('pick').hidden = !on
  if (!on) { $('batchBox').hidden = true; return }
  $('who').textContent = s.user || ''
  showCurrent(s.current)
  showBatch()
  search()
}

function escapeHtml(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])) }

async function proxyUrl(c) {
  const { server } = await chrome.storage.local.get('server')
  return c.image_proxy ? server.replace(/\/+$/, '') + c.image_proxy : c.image
}

function showCurrent(c) {
  const box = $('current')
  if (!c) { box.innerHTML = '<span class="muted">在下面勾选要找货源的商品。</span>'; return }
  box.innerHTML = `<div class="cur"><img src="${escapeHtml(c.image)}"><div style="flex:1;min-width:0">
    <div class="t" style="font-weight:600">${escapeHtml(c.title)}</div>
    <div class="muted">当前商品 · ${escapeHtml(c.store || '')} · ${c.specs.length} 个规格</div></div></div>
    <div class="row" style="margin-top:8px"><button id="openOne">只找这一个</button>
    <button class="plain" id="copyImg">复制主图链接</button></div>`
  $('openOne').onclick = () => send({ type: 'startBatch', items: [c] }).then(showBatch)
  $('copyImg').onclick = async () => { await navigator.clipboard.writeText(await proxyUrl(c)); $('copyImg').textContent = '已复制（1688 可下载）' }
}

async function showBatch() {
  const { batch } = await chrome.storage.local.get('batch')
  const box = $('batchBox')
  if (!batch || !batch.items.length) { box.hidden = true; return }
  box.hidden = false
  const done = batch.items.filter((x) => x.status === 'done').length
  $('batchSum').textContent = `已完成 ${done}/${batch.items.length}`
  $('batchList').innerHTML = ''
  for (const it of batch.items) {
    const div = document.createElement('div')
    div.className = 'item'
    div.title = it.note || ''
    div.innerHTML = `<img src="${escapeHtml(it.image)}"><div class="t">${escapeHtml(it.title)}
      ${it.note ? `<div class="muted">${escapeHtml(it.note)}</div>` : ''}</div><span class="st ${it.status}">${STATUS[it.status] || it.status}</span>`
    div.onclick = () => send({ type: 'focusTab', key: it.key })
    $('batchList').appendChild(div)
  }
}

function updatePicked() {
  $('batchGo').textContent = `批量找货源（${picked.size}）`
  $('batchGo').disabled = !picked.size
}

async function search() {
  const kind = $('kind').value
  const q = $('q').value.trim()
  const params = new URLSearchParams({ kind, q, missing: q ? 'false' : 'true', limit: '50' })
  if (kind === 'store' && $('store').value) params.set('store_id', $('store').value)
  $('list').innerHTML = '<p class="muted">加载中…</p>'
  try {
    const data = await call('GET', '/api/ext/queue?' + params)
    if (data.stores && $('store').options.length === 1) for (const st of data.stores) $('store').add(new Option(st.name, st.id))
    items = data.items
    renderList()
  } catch (e) { $('list').innerHTML = `<p class="err">${escapeHtml(e.message)}</p>` }
}

function renderList() {
  $('list').innerHTML = items.length ? '' : '<p class="muted">没有找到商品（默认只显示还没有货源的商品）</p>'
  for (const it of items) {
    const key = `${it.kind}:${it.id}`
    const div = document.createElement('div')
    div.className = 'item' + (picked.has(key) ? ' on' : '')
    div.innerHTML = `<input type="checkbox" ${picked.has(key) ? 'checked' : ''}><img src="${escapeHtml(it.image)}">
      <div class="t">${escapeHtml(it.title)}<div class="muted">${escapeHtml(it.store)} · ¥${yuan(it.price_min)}</div></div>`
    div.onclick = () => {
      if (picked.has(key)) picked.delete(key)
      else if (picked.size >= MAX) return alert(`一次最多 ${MAX} 个商品`)
      else picked.set(key, it)
      chrome.storage.local.set({ current: it }); showCurrent(it)
      renderList(); updatePicked()
    }
    $('list').appendChild(div)
  }
  updatePicked()
}

$('doLogin').onclick = async () => {
  $('loginErr').textContent = ''
  const server = $('server').value.trim().replace(/\/+$/, '').replace(/(\/#.*|\/api.*)$/, '')
  if (!/^https?:\/\//.test(server)) { $('loginErr').textContent = '网站地址要以 http:// 或 https:// 开头'; return }
  await chrome.storage.local.set({ server, token: '' })
  try {
    const data = await call('POST', '/api/auth/login', { username: $('username').value.trim(), password: $('password').value })
    await chrome.storage.local.set({ token: data.token, user: (data.user && (data.user.display_name || data.user.username)) || $('username').value.trim() })
    init()
  } catch (e) { $('loginErr').textContent = e.message }
}
$('logout').onclick = async () => { await chrome.storage.local.set({ token: '', current: null, batch: null }); init() }
$('search').onclick = search
$('q').onkeydown = (e) => { if (e.key === 'Enter') search() }
$('kind').onchange = () => { $('store').disabled = $('kind').value !== 'store'; search() }
$('store').onchange = search
$('checkAll').onchange = () => {
  picked.clear()
  if ($('checkAll').checked) for (const it of items.slice(0, MAX)) picked.set(`${it.kind}:${it.id}`, it)
  renderList()
}
$('batchGo').onclick = async () => {
  const list = [...picked.values()]
  $('batchGo').disabled = true
  try { await send({ type: 'startBatch', items: list }) } catch (e) { alert(e.message) }
  picked.clear(); $('checkAll').checked = false; renderList(); showBatch()
}
$('batchClear').onclick = async () => { await chrome.storage.local.set({ batch: null, tabs: {} }); showBatch() }
chrome.storage.onChanged.addListener((ch) => { if (ch.batch) showBatch() })
init()

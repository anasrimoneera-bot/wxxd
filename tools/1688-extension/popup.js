/* 1688 货源助手 — popup: website address + login, picking products (up to 10) and the batch progress. */
const $ = (id) => document.getElementById(id)
const send = (msg) => new Promise((resolve, reject) =>
  chrome.runtime.sendMessage(msg, (r) => (r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错')))))
const call = (method, path, body) => send({ type: 'api', method, path, body })
const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
const MAX = 10
const STATUS = { waiting: '等待', searching: '打开中', selecting: '请选择货源', uploaded: '搜索中', confirm: '已选，待设货源', done: '已设货源', nomatch: '已跳过', error: '出错' }
let items = []
const picked = new Map()

/* saved logins on this computer: [{server, username, password}] (password only with 记住密码) */
const loginId = (l) => `${l.server}|${l.username}`
async function showSaved(pick) {
  const { logins = [], loginUser } = await chrome.storage.local.get(['logins', 'loginUser'])
  $('savedBox').hidden = !logins.length
  $('saved').innerHTML = logins.map((l, i) => `<option value="${i}">${escapeHtml(l.username)} @ ${escapeHtml(l.server.replace(/^https?:\/\//, ''))}${l.password ? '' : '（未存密码）'}</option>`).join('')
  const want = pick || loginUser
  let idx = want ? logins.findIndex((l) => loginId(l) === loginId(want)) : -1
  if (idx < 0 && logins.length) idx = 0
  if (idx >= 0) { $('saved').value = String(idx); fillLogin(logins[idx]) }
}
function fillLogin(l) {
  $('server').value = l.server || ''
  $('username').value = l.username || ''
  $('password').value = l.password || ''
  $('remember').checked = l.password ? true : $('remember').checked
}

async function init() {
  const s = await chrome.storage.local.get(['server', 'token', 'user', 'current'])
  $('server').value = s.server || ''
  await showSaved()
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
    <div class="row" style="margin-top:8px"><button id="openOne">只找这一个</button></div>`
  $('openOne').onclick = () => send({ type: 'startBatch', items: [c], query: { kind: c.kind } }).then(showBatch)
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
  const username = $('username').value.trim(), password = $('password').value
  try {
    const data = await call('POST', '/api/auth/login', { username, password })
    const { logins = [] } = await chrome.storage.local.get('logins')
    const entry = { server, username, password: $('remember').checked ? password : '' }
    const list = [entry, ...logins.filter((l) => loginId(l) !== loginId(entry))].slice(0, 10)
    await chrome.storage.local.set({ token: data.token, logins: list, loginUser: { server, username },
      user: (data.user && (data.user.display_name || data.user.username)) || username })
    init()
  } catch (e) { $('loginErr').textContent = e.message }
}
$('saved').onchange = async () => {
  const { logins = [] } = await chrome.storage.local.get('logins')
  const l = logins[Number($('saved').value)]
  if (l) fillLogin(l)
}
$('delSaved').onclick = async () => {
  const { logins = [] } = await chrome.storage.local.get('logins')
  logins.splice(Number($('saved').value), 1)
  await chrome.storage.local.set({ logins })
  $('password').value = ''
  showSaved()
}
$('password').onkeydown = (e) => { if (e.key === 'Enter') $('doLogin').click() }
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
  const query = { kind: $('kind').value, store_id: $('kind').value === 'store' ? $('store').value : '' }
  try {
    const r = await send({ type: 'startBatch', items: list, query })
    if (r && r.taken) alert(`有 ${r.taken} 个商品正在被其他账号找货源，已跳过；这一批是 ${r.count} 个。`)
  } catch (e) { alert(e.message) }
  picked.clear(); $('checkAll').checked = false; renderList(); showBatch()
}
chrome.storage.local.get('searchPage').then(({ searchPage }) => { $('searchPage').value = searchPage || 'channel' })
$('searchPage').onchange = () => chrome.storage.local.set({ searchPage: $('searchPage').value })
$('batchClear').onclick = async () => { await send({ type: 'clearBatch' }).catch(() => {}); showBatch(); search() }
chrome.storage.onChanged.addListener((ch) => { if (ch.batch) showBatch() })
init()

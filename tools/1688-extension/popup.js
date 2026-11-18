/* 1688 货源助手 — popup: website address + login, and picking the product to find a 货源 for. */
const $ = (id) => document.getElementById(id)
const call = (method, path, body) => new Promise((resolve, reject) =>
  chrome.runtime.sendMessage({ type: 'api', method, path, body }, (r) => (r && r.ok ? resolve(r.data) : reject(new Error(r ? r.error : '插件出错')))))
const yuan = (fen) => (fen == null ? '-' : (fen / 100).toFixed(2))
const AIBUY = 'https://aibuy.1688.com/landingpage/home/inventory/products.html?bizType=selectionTool&customerId=sellerspriteLP&lang=zh&currency=CNY'

async function init() {
  const s = await chrome.storage.local.get(['server', 'token', 'user', 'current'])
  $('server').value = s.server || ''
  if (!s.token) { $('login').hidden = false; $('main').hidden = true; $('pick').hidden = true; return }
  $('login').hidden = true; $('main').hidden = false; $('pick').hidden = false
  $('who').textContent = s.user || ''
  showCurrent(s.current)
  search()
}

function showCurrent(c) {
  const box = $('current')
  if (!c) { box.innerHTML = '<span class="muted">还没有选择商品：在下面选一个要找货源的商品。</span>'; return }
  box.innerHTML = `<div class="cur"><img src="${c.image || ''}"><div style="flex:1;min-width:0">
    <div class="t" style="font-weight:600">${escapeHtml(c.title)}</div>
    <div class="muted">${escapeHtml(c.store || '')} · ${c.specs.length} 个规格</div></div></div>
    <div class="row" style="margin-top:8px"><button id="openAibuy">打开 1688 找货源</button>
    <button class="plain" id="copyImg">复制主图链接</button></div>
    <p class="muted">在 1688 页面点「图片链接搜」，粘贴主图链接即可搜同款。</p>`
  $('openAibuy').onclick = async () => { await navigator.clipboard.writeText(c.image || '').catch(() => {}); chrome.tabs.create({ url: AIBUY }) }
  $('copyImg').onclick = async () => { await navigator.clipboard.writeText(c.image || ''); $('copyImg').textContent = '已复制' }
}

function escapeHtml(s) { return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])) }

async function search() {
  const kind = $('kind').value
  const params = new URLSearchParams({ kind, q: $('q').value.trim(), missing: $('q').value.trim() ? 'false' : 'true', limit: '50' })
  if (kind === 'store' && $('store').value) params.set('store_id', $('store').value)
  $('list').innerHTML = '<p class="muted">加载中…</p>'
  try {
    const data = await call('GET', '/api/ext/queue?' + params)
    if (data.stores && $('store').options.length === 1) {
      for (const st of data.stores) $('store').add(new Option(st.name, st.id))
    }
    const { current } = await chrome.storage.local.get('current')
    $('list').innerHTML = data.items.length ? '' : '<p class="muted">没有找到商品</p>'
    for (const it of data.items) {
      const div = document.createElement('div')
      div.className = 'item' + (current && current.kind === it.kind && current.id === it.id ? ' on' : '')
      div.innerHTML = `<img src="${it.image || ''}"><div class="t">${escapeHtml(it.title)}<div class="muted">${escapeHtml(it.store)} · ¥${yuan(it.price_min)}</div></div>`
      div.onclick = async () => { await chrome.storage.local.set({ current: it }); showCurrent(it); search() }
      $('list').appendChild(div)
    }
  } catch (e) { $('list').innerHTML = `<p class="err">${escapeHtml(e.message)}</p>` }
}

$('doLogin').onclick = async () => {
  $('loginErr').textContent = ''
  const server = $('server').value.trim().replace(/\/+$/, '')
  if (!/^https?:\/\//.test(server)) { $('loginErr').textContent = '网站地址要以 http:// 或 https:// 开头'; return }
  await chrome.storage.local.set({ server, token: '' })
  try {
    const data = await call('POST', '/api/auth/login', { username: $('username').value.trim(), password: $('password').value })
    await chrome.storage.local.set({ token: data.token, user: (data.user && (data.user.display_name || data.user.username)) || $('username').value.trim() })
    init()
  } catch (e) { $('loginErr').textContent = e.message }
}
$('logout').onclick = async () => { await chrome.storage.local.set({ token: '', current: null }); init() }
$('search').onclick = search
$('q').onkeydown = (e) => { if (e.key === 'Enter') search() }
$('kind').onchange = () => { $('store').disabled = $('kind').value !== 'store'; search() }
$('store').onchange = search
init()

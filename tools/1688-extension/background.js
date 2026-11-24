/* 1688 货源助手 — background service worker.
 * - All calls to the 微信小店助手 server go through here (not limited by the 1688 page's CORS rules).
 * - 批量找货源: up to 10 products searched on one 找品 page; each chosen 货源 opens in its own tab. The tab ->
 *   product map lives in storage (the service worker can be stopped at any time).
 * storage.local: server, token, user, current (the product picked in the popup),
 *   batch: {items: [{...item, key, status, note, best}]}, tabs: {tabId: key} */
// where 批量找货源 searches the main images (chosen in the popup; storage.local.searchPage)
const SEARCH_URLS = {
  channel: 'https://air.1688.com/app/channel-fe/search/index.html#/result',  // 一件代发 搜索 → 搜图片 (default)
  find: 'https://air.1688.com/app/1688-lp/landing-page/new-home/find-products.html?bizType=selectionTool&customerId=sellerspriteLP',  // 找品 → 批量图搜
}
async function searchUrl() {
  const { searchPage } = await chrome.storage.local.get('searchPage')
  return SEARCH_URLS[searchPage] || SEARCH_URLS.channel
}
const MAX_BATCH = 10

/* the login expired: log in again with the saved password (记住密码) of the same website + account */
async function relogin() {
  const { server, loginUser, logins = [] } = await chrome.storage.local.get(['server', 'loginUser', 'logins'])
  const l = loginUser && logins.find((x) => x.server === server && x.username === loginUser.username && x.password)
  if (!l) return false
  const res = await fetch(server.replace(/\/+$/, '') + '/api/auth/login', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: l.username, password: l.password }) })
  if (!res.ok) return false
  const data = await res.json()
  await chrome.storage.local.set({ token: data.token })
  return true
}

async function api(method, path, body, retried) {
  const { server, token } = await chrome.storage.local.get(['server', 'token'])
  if (!server) throw new Error('请先在插件弹窗里填写网站地址并登录')
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), 120000)  // never wait for ever
  const res = await fetch(server.replace(/\/+$/, '') + path, {
    signal: ctl.signal,
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch (e) { /* not JSON */ } finally { clearTimeout(timer) }
  if (res.status === 401 && !retried && path !== '/api/auth/login' && await relogin().catch(() => false)) {
    return api(method, path, body, true)  // logged in again with the saved password
  }
  if (res.status === 401) throw new Error(path === '/api/auth/login' ? ((data && data.detail) || '账号或密码错误') : '登录已过期，请在插件弹窗里重新登录')
  if (!res.ok) {
    const d = data && data.detail
    throw new Error(typeof d === 'string' ? d : (d ? JSON.stringify(d) : `请求失败（${res.status}）`))
  }
  return data
}

const keyOf = (it) => `${it.kind}:${it.id}`
/* 第二阶段: the offer found in 第一阶段 is where 「打开货源」 starts */
const hintBest = (it) => (it.hint && it.hint.offer_id ? { offer_id: it.hint.offer_id, price: it.hint.price, title: it.hint.title } : null)

/* the product's main image from our server (public signed link), as a data: URL */
async function fetchImage(item) {
  const { server } = await chrome.storage.local.get('server')
  const url = item.image_proxy ? server.replace(/\/+$/, '') + item.image_proxy : item.image
  const res = await fetch(url)
  if (!res.ok) throw new Error(`读取主图失败（${res.status}）`)
  const blob = await res.blob()
  return await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = () => reject(new Error('读取主图失败'))
    r.readAsDataURL(blob)
  })
}

/* 批量找货源: one 找品 page searches all the products' main images (批量图搜, up to 10) */
const BATCH = '*batch*'
/* claims: products someone else (another account) is already working on are not taken */
async function claimSome(cands, want) {
  const got = [], taken = []
  let i = 0
  while (got.length < want && i < cands.length) {  // ask for as many as are still missing
    const part = cands.slice(i, i + (want - got.length))
    i += part.length
    const r = await api('POST', '/api/ext/claim', { items: part.map((it) => ({ kind: it.kind, id: it.id })) })
    const ok = new Set(r.claimed || [])
    for (const it of part) (ok.has(keyOf(it)) ? got : taken).push(it)
  }
  return { got, taken }
}
async function releaseBatch(batch) {
  const left = ((batch && batch.items) || []).filter((x) => x.status !== 'done' && x.status !== 'nomatch')
  if (left.length) await api('POST', '/api/ext/release', { items: left.map((it) => ({ kind: it.kind, id: it.id })) }).catch(() => {})
}

async function startBatch(items, query) {
  const { batch: prev } = await chrome.storage.local.get('batch')
  await releaseBatch(prev)
  const c = await claimSome(items.slice(0, MAX_BATCH), MAX_BATCH)
  if (!c.got.length) throw new Error(`这些商品正在被其他账号找货源（${c.taken.length} 个），请换别的商品`)
  items = c.got.map((it) => ({ ...it, key: keyOf(it), status: 'waiting', note: '', best: hintBest(it) }))
  const { tabs: old = {} } = await chrome.storage.local.get('tabs')
  await chrome.storage.local.set({ batch: { items, started: Date.now(), query: query || null }, tabs: {}, current: items[0] || null })
  for (const tid of Object.keys(old)) if (old[tid] === BATCH) chrome.tabs.remove(Number(tid)).catch(() => {})
  const tab = await chrome.tabs.create({ url: await searchUrl(), active: true })
  await chrome.storage.local.set({ tabs: { [tab.id]: BATCH } })
  return { count: items.length, taken: c.taken.length }
}

/* the next 10 products without a 货源 (same list as the batch was started from), on the same 找品 page */
async function nextBatch(tabId) {
  const { batch } = await chrome.storage.local.get('batch')
  const q = (batch && batch.query) || { kind: 'store' }
  const params = new URLSearchParams({ kind: q.kind || 'store', missing: 'true', limit: '50', stage: String(q.stage || 1) })
  if (q.store_id) params.set('store_id', q.store_id)
  const data = await api('GET', '/api/ext/queue?' + params)
  const seen = new Set(((batch && batch.items) || []).map((x) => x.key))
  await releaseBatch(batch)
  const cands = (data.items || []).filter((it) => !seen.has(keyOf(it)))
  const items = (await claimSome(cands, MAX_BATCH)).got  // not the ones another account is on
    .map((it) => ({ ...it, key: keyOf(it), status: 'waiting', note: '', best: hintBest(it) }))
  if (!items.length) return { none: true }
  await chrome.storage.local.set({ batch: { items, started: Date.now(), query: q, round: ((batch && batch.round) || 1) + 1 },
    tabs: { [tabId]: BATCH }, current: items[0] })
  // a fresh 找品 page: room for 10 new pictures. The 一件代发 link ends in #/result: the same link again only
  // changes the hash and the page is not loaded again (it kept the old, finished batch and asked for the next
  // one over and over), so the link is made new each time
  const url = (await searchUrl()).replace(/^([^#?]*)(\?[^#]*)?/, (m, path, qs) => `${path}${qs ? qs + '&' : '?'}hybatch=${Date.now()}`)
  await chrome.tabs.update(tabId, { url })
  return { count: items.length }
}

async function myBatch(tabId) {
  const { tabs = {}, batch } = await chrome.storage.local.get(['tabs', 'batch'])
  return tabs[tabId] === BATCH && batch ? batch : null
}

async function openDetail(key, url) {  // the chosen offer of one product, in its own tab
  const tab = await chrome.tabs.create({ url, active: true })
  const { tabs = {} } = await chrome.storage.local.get('tabs')
  tabs[tab.id] = key
  await chrome.storage.local.set({ tabs })
}

async function myItem(tabId) {
  const { tabs = {}, batch } = await chrome.storage.local.get(['tabs', 'batch'])
  const key = tabs[tabId]
  return (key && key !== BATCH && batch && batch.items.find((x) => x.key === key)) || null
}

async function setStatus(tabId, patch) {
  const { tabs = {}, batch } = await chrome.storage.local.get(['tabs', 'batch'])
  const key = patch.key || tabs[tabId]
  if (!key || !batch) return null
  const it = batch.items.find((x) => x.key === key)
  if (!it) return null
  Object.assign(it, patch.data || {})
  if (patch.data && patch.data.status === 'selecting') batch.active = key  // the product being chosen on the 找品 page
  await chrome.storage.local.set({ batch })
  return it
}

chrome.tabs.onCreated.addListener(async (tab) => {  // a result opened in a new tab keeps the product
  if (!tab.openerTabId) return
  const { tabs = {} } = await chrome.storage.local.get('tabs')
  const { batch } = await chrome.storage.local.get('batch')
  if (tabs[tab.openerTabId] === BATCH && batch && batch.active && !tabs[tab.id]) {
    // a result opened from the 找品 page belongs to the product being chosen there
    tabs[tab.id] = batch.active; await chrome.storage.local.set({ tabs })
  } else if (tabs[tab.openerTabId] && tabs[tab.openerTabId] !== BATCH && !tabs[tab.id]) { tabs[tab.id] = tabs[tab.openerTabId]; await chrome.storage.local.set({ tabs }) }
})
/* 订单管理「1688查看」 opens the 1688 order with #hyship=<order>: kept per tab, as 1688 may redirect and drop it */
/* 网站「找货源」 (商品云库 / 商品管理): a 1688 link with #hyfind=<kind>:<ids> starts a batch with these products */
const findDone = new Set()
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  const m = (info.url || '').match(/hyfind=(store|library):([\d,]+)/)
  if (!m || findDone.has(tabId)) return
  findDone.add(tabId)
  try {
    const ids = m[2].split(',').filter(Boolean).slice(0, MAX_BATCH)
    const items = []
    for (const id of ids) items.push(await api('GET', `/api/ext/item?kind=${m[1]}&id=${id}`))
    if (items.length) await startBatch(items, { kind: m[1], stage: 1 })
    chrome.tabs.remove(tabId).catch(() => {})  // the batch opened its own 1688 page
  } catch (e) {
    chrome.tabs.update(tabId, { url: 'data:text/html;charset=utf-8,' + encodeURIComponent(`<meta charset="utf-8"><p style="font:16px sans-serif;padding:20px">1688 货源助手：${e.message}</p>`) }).catch(() => {})
  }
})
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  const m = (info.url || '').match(/hyship=(\d+)/)
  if (!m) return
  const { ships = {} } = await chrome.storage.local.get('ships')
  for (const k of Object.keys(ships)) if (Date.now() - ships[k].at > 3600000) delete ships[k]
  ships[tabId] = { id: Number(m[1]), at: Date.now() }
  await chrome.storage.local.set({ ships })
})
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { tabs = {} } = await chrome.storage.local.get('tabs')
  if (tabs[tabId]) { delete tabs[tabId]; await chrome.storage.local.set({ tabs }) }
})

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = sender.tab && sender.tab.id
  const run = {
    api: () => api(msg.method || 'GET', msg.path, msg.body),
    startBatch: () => startBatch(msg.items || [], msg.query),
    nextBatch: () => nextBatch(tabId),
    clearBatch: async () => { const { batch } = await chrome.storage.local.get('batch'); await releaseBatch(batch); await chrome.storage.local.set({ batch: null, tabs: {} }); return true },
    myItem: () => myItem(tabId),
    myBatch: () => myBatch(tabId),
    closeMe: async () => {  // a detail page after 设为货源: close it and go back to the 找品 page
      const { tabs = {} } = await chrome.storage.local.get('tabs')
      const back = Object.keys(tabs).find((t) => tabs[t] === BATCH)
      if (back) { try { const t = await chrome.tabs.update(Number(back), { active: true }); await chrome.windows.update(t.windowId, { focused: true }) } catch (e) { /* closed */ } }
      if (tabId) await chrome.tabs.remove(tabId)
      return true
    },
    openDetail: () => openDetail(msg.key, msg.url),
    setStatus: () => setStatus(tabId, msg),
    fetchImage: () => fetchImage(msg.item),
    myShip: async () => {
      const { ships = {} } = await chrome.storage.local.get('ships')
      const s = ships[tabId]
      return s && Date.now() - s.at < 3600000 ? s : null
    },
    focusTab: async () => {
      const { tabs = {} } = await chrome.storage.local.get('tabs')
      const tid = Object.keys(tabs).find((t) => tabs[t] === msg.key)
      if (tid) { const t = await chrome.tabs.update(Number(tid), { active: true }); await chrome.windows.update(t.windowId, { focused: true }); return true }
      return false
    },
  }[msg && msg.type]
  if (!run) return false
  run().then((data) => reply({ ok: true, data })).catch((e) => reply({ ok: false, error: String(e.message || e) }))
  return true  // async reply
})

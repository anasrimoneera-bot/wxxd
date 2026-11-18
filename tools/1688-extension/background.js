/* 1688 货源助手 — background service worker.
 * - All calls to the 微信小店助手 server go through here (not limited by the 1688 page's CORS rules).
 * - 批量找货源: up to 10 products searched on one 找品 page; each chosen 货源 opens in its own tab. The tab ->
 *   product map lives in storage (the service worker can be stopped at any time).
 * storage.local: server, token, user, current (the product picked in the popup),
 *   batch: {items: [{...item, key, status, note, best}]}, tabs: {tabId: key} */
const SEARCH_URL = 'https://air.1688.com/app/1688-lp/landing-page/new-home/find-products.html?bizType=selectionTool&customerId=sellerspriteLP'
const MAX_BATCH = 10

async function api(method, path, body) {
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
  if (res.status === 401) throw new Error('登录已过期，请在插件弹窗里重新登录')
  if (!res.ok) {
    const d = data && data.detail
    throw new Error(typeof d === 'string' ? d : (d ? JSON.stringify(d) : `请求失败（${res.status}）`))
  }
  return data
}

const keyOf = (it) => `${it.kind}:${it.id}`

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
async function startBatch(items, query) {
  items = items.slice(0, MAX_BATCH).map((it) => ({ ...it, key: keyOf(it), status: 'waiting', note: '', best: null }))
  const { tabs: old = {} } = await chrome.storage.local.get('tabs')
  await chrome.storage.local.set({ batch: { items, started: Date.now(), query: query || null }, tabs: {}, current: items[0] || null })
  for (const tid of Object.keys(old)) if (old[tid] === BATCH) chrome.tabs.remove(Number(tid)).catch(() => {})
  const tab = await chrome.tabs.create({ url: SEARCH_URL, active: true })
  await chrome.storage.local.set({ tabs: { [tab.id]: BATCH } })
}

/* the next 10 products without a 货源 (same list as the batch was started from), on the same 找品 page */
async function nextBatch(tabId) {
  const { batch } = await chrome.storage.local.get('batch')
  const q = (batch && batch.query) || { kind: 'store' }
  const params = new URLSearchParams({ kind: q.kind || 'store', missing: 'true', limit: '50' })
  if (q.store_id) params.set('store_id', q.store_id)
  const data = await api('GET', '/api/ext/queue?' + params)
  const seen = new Set(((batch && batch.items) || []).map((x) => x.key))
  const items = (data.items || []).filter((it) => !seen.has(keyOf(it))).slice(0, MAX_BATCH)
    .map((it) => ({ ...it, key: keyOf(it), status: 'waiting', note: '', best: null }))
  if (!items.length) return { none: true }
  await chrome.storage.local.set({ batch: { items, started: Date.now(), query: q, round: ((batch && batch.round) || 1) + 1 },
    tabs: { [tabId]: BATCH }, current: items[0] })
  await chrome.tabs.update(tabId, { url: SEARCH_URL })  // a fresh 找品 page: room for 10 new pictures
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

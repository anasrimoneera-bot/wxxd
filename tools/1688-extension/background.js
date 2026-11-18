/* 1688 货源助手 — background service worker.
 * - All calls to the 微信小店助手 server go through here (not limited by the 1688 page's CORS rules).
 * - 批量找货源: up to 10 products, one 1688 tab each. The tab -> product map lives in storage
 *   (the service worker can be stopped at any time), a tab opened from a batch tab inherits its product.
 * storage.local: server, token, user, current (the product picked in the popup),
 *   batch: {items: [{...item, key, status, note, best}]}, tabs: {tabId: key} */
const SEARCH_URL = 'https://www.1688.com/'
const MAX_BATCH = 10

async function api(method, path, body) {
  const { server, token } = await chrome.storage.local.get(['server', 'token'])
  if (!server) throw new Error('请先在插件弹窗里填写网站地址并登录')
  const res = await fetch(server.replace(/\/+$/, '') + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch (e) { /* not JSON */ }
  if (res.status === 401) throw new Error('登录已过期，请在插件弹窗里重新登录')
  if (!res.ok) {
    const d = data && data.detail
    throw new Error(typeof d === 'string' ? d : (d ? JSON.stringify(d) : `请求失败（${res.status}）`))
  }
  return data
}

const keyOf = (it) => `${it.kind}:${it.id}`
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

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

async function startBatch(items) {
  items = items.slice(0, MAX_BATCH).map((it) => ({ ...it, key: keyOf(it), status: 'waiting', note: '', best: null }))
  const { tabs: old = {} } = await chrome.storage.local.get('tabs')
  await chrome.storage.local.set({ batch: { items, started: Date.now() }, tabs: {}, current: items[0] || null })
  for (const tid of Object.keys(old)) chrome.tabs.remove(Number(tid)).catch(() => {})
  for (let i = 0; i < items.length; i++) {  // one tab at a time, a few seconds apart, like a person would
    const tab = await chrome.tabs.create({ url: SEARCH_URL, active: i === 0 })
    const { tabs = {} } = await chrome.storage.local.get('tabs')
    tabs[tab.id] = items[i].key
    await chrome.storage.local.set({ tabs })
    if (i < items.length - 1) await sleep(3000)
  }
}

async function myItem(tabId) {
  const { tabs = {}, batch } = await chrome.storage.local.get(['tabs', 'batch'])
  const key = tabs[tabId]
  return (key && batch && batch.items.find((x) => x.key === key)) || null
}

async function setStatus(tabId, patch) {
  const { tabs = {}, batch } = await chrome.storage.local.get(['tabs', 'batch'])
  const key = patch.key || tabs[tabId]
  if (!key || !batch) return null
  const it = batch.items.find((x) => x.key === key)
  if (!it) return null
  Object.assign(it, patch.data || {})
  await chrome.storage.local.set({ batch })
  return it
}

chrome.tabs.onCreated.addListener(async (tab) => {  // a result opened in a new tab keeps the product
  if (!tab.openerTabId) return
  const { tabs = {} } = await chrome.storage.local.get('tabs')
  if (tabs[tab.openerTabId] && !tabs[tab.id]) { tabs[tab.id] = tabs[tab.openerTabId]; await chrome.storage.local.set({ tabs }) }
})
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { tabs = {} } = await chrome.storage.local.get('tabs')
  if (tabs[tabId]) { delete tabs[tabId]; await chrome.storage.local.set({ tabs }) }
})

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tabId = sender.tab && sender.tab.id
  const run = {
    api: () => api(msg.method || 'GET', msg.path, msg.body),
    startBatch: () => startBatch(msg.items || []),
    myItem: () => myItem(tabId),
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

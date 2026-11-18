/* 1688 货源助手 — all calls to the 微信小店助手 server go through here (a service worker is not
 * limited by the 1688 page's CORS rules). Settings live in chrome.storage.local:
 * server (e.g. https://your-site.com), token, user, current ({kind, id, title, image, specs}). */
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

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === 'api') {
    api(msg.method || 'GET', msg.path, msg.body).then((data) => reply({ ok: true, data }))
      .catch((e) => reply({ ok: false, error: String(e.message || e) }))
    return true  // async reply
  }
  return false
})

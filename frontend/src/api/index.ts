import axios from 'axios'
import { ElMessage } from 'element-plus'

export const http = axios.create({ baseURL: '/api', timeout: 120000 })

http.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('token')
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

http.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err.response?.status
    let msg = err.response?.data?.detail ?? err.message
    if (Array.isArray(msg)) msg = msg.map((d: any) => d.msg).join('；')
    if (status === 401) {
      localStorage.removeItem('token')
      if (location.pathname !== '/login') location.href = '/login'
    }
    ElMessage.error(String(msg))
    return Promise.reject(err)
  },
)

export async function download(url: string, params: Record<string, any> = {}, filename = 'export.xlsx') {
  const r = await http.get(url, { params, responseType: 'blob' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(r.data)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

export const yuan = (fen: number) => (fen / 100).toFixed(2)
export const priceRange = (a: number, b: number) => (a === b ? `¥${yuan(a)}` : `¥${yuan(a)} - ¥${yuan(b)}`)

export const PLATFORMS = [
  { key: 'taobao', name: '淘宝', short: '淘', color: '#ff6a00' },
  { key: '1688', name: '1688', short: '1688', color: '#ff4f00' },
  { key: 'pdd', name: '拼多多', short: '拼', color: '#e02e24' },
  { key: 'kuaishou', name: '快手电商', short: '快', color: '#fe3666' },
  { key: 'wechat', name: '微信小店', short: '微', color: '#07c160' },
]

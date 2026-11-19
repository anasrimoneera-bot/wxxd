// 微信小店「电子面单打印组件」: runs on this PC and listens on ws://127.0.0.1:12705. JSON commands,
// answered with the same requestID: getPrinterList, getAppInfo, preview, print.
const URL = 'ws://127.0.0.1:12705'
let ws: WebSocket | null = null
let opening: Promise<WebSocket> | null = null
const waiting = new Map<string, (resp: any) => void>()
let seq = 0

function connect(): Promise<WebSocket> {
  if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve(ws)
  if (opening) return opening
  opening = new Promise((resolve, reject) => {
    const s = new WebSocket(URL)
    const timer = setTimeout(() => { s.close(); reject(new Error('连不上打印组件：请确认这台电脑已安装并打开「微信小店打印组件」')) }, 4000)
    s.onopen = () => { clearTimeout(timer); ws = s; resolve(s) }
    s.onerror = () => { clearTimeout(timer); reject(new Error('连不上打印组件：请确认这台电脑已安装并打开「微信小店打印组件」')) }
    s.onclose = () => { ws = null }
    s.onmessage = (e) => {
      let resp: any = {}
      try { resp = JSON.parse(e.data || '{}') } catch { return }
      const done = waiting.get(resp.requestID)
      if (done) { waiting.delete(resp.requestID); done(resp) }
    }
  }).finally(() => { opening = null }) as Promise<WebSocket>
  return opening
}

async function send(cmd: any, timeoutMs = 60000): Promise<any> {
  const s = await connect()
  const requestID = `req_${Date.now()}_${++seq}`
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiting.delete(requestID); reject(new Error('打印组件没有响应')) }, timeoutMs)
    waiting.set(requestID, (r) => { clearTimeout(timer); resolve(r) })
    s.send(JSON.stringify({ ...cmd, requestID }))
  })
}

export async function printerList(): Promise<{ name: string; displayName: string; status?: number }[]> {
  return (await send({ command: 'getPrinterList' }, 8000)).printerList || []
}

export interface PrintTask { taskID: string; printInfo: string }

/** Print 面单 (print_info from 取号); returns {taskID: true | failure reason}. */
export async function printWaybills(tasks: PrintTask[], printer?: string): Promise<Record<string, true | string>> {
  const resp = await send({
    command: 'print', version: '2.0', printType: 1, ...(printer ? { printer } : {}),
    taskList: tasks.map((t, i) => ({ taskID: t.taskID, printInfo: t.printInfo, printNum: { curNum: i + 1, sumNum: tasks.length } })),
  }, 120000)
  const out: Record<string, true | string> = {}
  for (const r of resp.results || []) out[r.taskID] = r.success ? true : (r.failureReason || '打印失败')
  return out
}

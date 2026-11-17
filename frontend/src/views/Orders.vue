<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">订单管理 <span class="muted">（从微信小店同步，可直接发货）</span></div>
        <el-button type="primary" icon="Refresh" :loading="syncing" @click="sync([])">同步全部店铺订单</el-button>
      </div>
      <div class="stores">
        <div v-for="s in stores" :key="s.id" class="scard" :class="{ active: q.store_id === s.id }"
             @click="q.store_id = q.store_id === s.id ? undefined : s.id; search()">
          <div class="b">{{ s.name }}</div>
          <div class="muted">{{ s.orders }} 个订单</div>
          <div class="small" :class="s.sync_status === 'failed' ? 'danger' : 'muted'">
            <el-icon v-if="s.sync_status === 'running'" class="is-loading"><Loading /></el-icon>
            {{ s.sync_message || '未同步' }}
          </div>
          <el-button link type="primary" size="small" :disabled="s.sync_status === 'running'" @click.stop="sync([s.id])">同步</el-button>
        </div>
      </div>
      <p class="muted small">首次同步读取最近 30 天的订单，之后每次只读取新增和有变化的订单。</p>
    </div>

    <div class="card">
      <el-tabs v-model="statusTab" @tab-change="search">
        <el-tab-pane v-for="t in TABS" :key="t.key" :name="t.key" :label="`${t.label}${t.key !== 'all' && counts[t.key] ? `（${counts[t.key]}）` : ''}`" />
      </el-tabs>
      <el-form :inline="true">
        <el-form-item label="搜索"><el-input v-model="q.keyword" placeholder="订单号 / 收件人 / 电话" clearable style="width: 220px" @keyup.enter="search" /></el-form-item>
        <el-form-item label="下单日期">
          <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束" style="width: 240px" />
        </el-form-item>
        <el-form-item><el-button type="primary" icon="Search" @click="search">搜索</el-button></el-form-item>
        <el-form-item>
          <el-button icon="View" :loading="revealingAll" :disabled="!items.some((o) => o.masked)" @click="revealPage">
            {{ revealingAll ? `获取中 ${revealDone}/${revealTotal}` : '显示本页买家信息' }}</el-button>
          <el-button type="success" plain icon="Van" @click="openBatch">批量发货</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="items" v-loading="loading" border>
        <el-table-column label="订单" width="210">
          <template #default="{ row }">
            <div class="b">{{ row.order_id }}</div>
            <div class="muted small">{{ row.store_name }}</div>
            <div class="muted small">下单 {{ row.create_time }}</div>
            <el-tag :type="statusType(row.status)" size="small">{{ row.status_name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="商品 / 货源" min-width="420">
          <template #default="{ row }">
            <div v-for="(it, i) in row.items" :key="i" class="line">
              <el-image :src="it.thumb" fit="cover" class="thumb" lazy />
              <div class="info">
                <div class="ellipsis" :title="it.title">{{ it.title }}
                  <el-link type="primary" :underline="false" class="small" @click="goProduct(row, it)">改售价</el-link></div>
                <div class="muted small">{{ it.attrs || '-' }} · ×{{ it.qty }} · ¥{{ yuan(it.real_price || it.sale_price) }}</div>
                <div class="small">
                  <template v-if="it.source">
                    货源：<a :href="it.source.detail_url" target="_blank" :title="it.source.supplier">¥{{ yuan(it.source.price) }} {{ it.source.supplier || '1688' }}</a>
                    <el-tag v-if="it.source.price_edited" size="small" type="warning" effect="plain" class="ml">已改价</el-tag>
                  </template>
                  <span v-else class="warn">未设置货源</span>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="150" align="right">
          <template #default="{ row }">
            <div>实付 <b>¥{{ yuan(row.order_price) }}</b></div>
            <div class="muted small">运费 ¥{{ yuan(row.freight) }}</div>
            <div v-if="row.purchase_cost != null" class="small">采购 ¥{{ yuan(row.purchase_cost) }}</div>
            <div v-else-if="row.source_cost != null" class="muted small">货源 ¥{{ yuan(row.source_cost) }}</div>
            <div v-if="row.margin != null" class="small" :class="row.margin >= 0 ? 'ok' : 'danger'">
              毛利 ¥{{ yuan(row.margin) }}<span v-if="!row.margin_real" class="muted">（预估）</span>
            </div>
            <div v-if="row.purchase_order_no" class="muted small ellipsis" :title="row.purchase_order_no">采购单 {{ row.purchase_order_no }}</div>
          </template>
        </el-table-column>
        <el-table-column label="收件人" min-width="220">
          <template #default="{ row }">
            <div>{{ row.receiver_name }} <span :class="{ muted: row.phone_masked }">{{ row.receiver_phone }}</span></div>
            <div v-if="row.virtual_phone" class="small">
              <el-tooltip content="买家使用了号码保护：请拨打这个虚拟号（转分机号）联系买家，填写快递单也用它" placement="top">
                <el-tag size="small" type="warning" effect="plain">虚拟号</el-tag>
              </el-tooltip>
              <b>{{ row.virtual_phone }}</b>
              <span v-if="row.virtual_phone_expire" class="muted">（{{ row.virtual_phone_expire.slice(0, 10) }} 到期）</span>
            </div>
            <div class="muted small">{{ row.receiver_address }}</div>
            <el-button v-if="row.masked" link type="primary" size="small" :loading="row._revealing" @click="reveal(row)">显示买家信息</el-button>
            <template v-else-if="row.receiver_name">
              <el-button link type="primary" size="small" @click="copyBuyer(row)">复制</el-button>
              <el-button v-if="row.phone_masked && !row.virtual_phone" link type="primary" size="small" :loading="row._revealing" @click="reveal(row, true)">重新获取</el-button>
              <el-button v-if="row.virtual_phone" link type="primary" size="small" @click="delayVirtual(row)">虚拟号延期</el-button>
              <el-button v-if="row.phone_masked" link type="primary" size="small" @click="applyReal(row)">申请真实号码</el-button>
              <el-button v-if="row._applied" link size="small" @click="checkReal(row)">查询审核</el-button>
            </template>
            <div v-if="row.buyer_note" class="warn small">买家留言：{{ row.buyer_note }}</div>
          </template>
        </el-table-column>
        <el-table-column label="物流" width="190">
          <template #default="{ row }">
            <div v-for="(d, i) in row.deliveries" :key="i" class="small">
              {{ companyName(d.delivery_id) }} <a :href="`https://www.kuaidi100.com/chaxun?nu=${d.waybill_id}`" target="_blank">{{ d.waybill_id }}</a>
              <div class="muted">{{ d.time }}</div>
            </div>
            <span v-if="!row.deliveries.length" class="muted small">未发货</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <div><el-button v-if="row.can_ship" type="primary" size="small" @click="openShip(row)">发货</el-button></div>
            <div><el-button link type="warning" size="small" @click="openPurchase(row)">{{ row.purchased_at ? '采购✓' : '采购' }}</el-button></div>
            <div><el-button v-if="row.deliveries.length" link type="primary" size="small" @click="openShip(row, true)">补发货</el-button></div>
            <div><el-button link type="primary" size="small" @click="showRaw(row)">原始数据</el-button></div>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]"
                       layout="total, sizes, prev, pager, next" @change="load" />
      </div>
      <p class="muted small">毛利 = 实付 − 货源价 × 数量（不含采购运费），只在每个商品都设置了货源时显示。</p>
      <p class="muted small">微信同步的订单中，买家姓名、电话、地址是打码的（如 郑**）。点「显示买家信息」通过微信接口获取完整信息并保存在订单里，之后不会重复获取（该接口每天有调用次数限制）。买家信息仅用于发货，请勿外传。</p>
    </div>

    <!-- 发货 / 补发货 -->
    <el-dialog v-model="shipVisible" :title="`${sf.compensation ? '补发货' : '发货'} · ${shipRow?.order_id || ''}`" width="520px">
      <el-form label-width="90px">
        <el-form-item label="发货方式">
          <el-radio-group v-model="sf.deliver_type"><el-radio :value="1">快递</el-radio><el-radio :value="3">无需物流（虚拟商品）</el-radio></el-radio-group>
        </el-form-item>
        <template v-if="sf.deliver_type === 1">
          <el-form-item label="快递公司" required>
            <el-select v-model="sf.delivery_id" filterable placeholder="选择或搜索快递公司" style="width: 100%">
              <el-option v-for="c in companies" :key="c.id" :label="`${c.name}（${c.id}）`" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="运单号" required><el-input v-model="sf.waybill_id" placeholder="快递单号" /></el-form-item>
        </template>
        <el-form-item v-if="sf.compensation" label="补发原因"><el-input v-model="sf.reason" placeholder="如：漏发、拆分包裹、坏损补发、赠品" /></el-form-item>
        <el-form-item label="商品">
          <div class="muted small">{{ sf.compensation ? '补发订单内全部商品（每单最多补发 10 次，不计入发货考核）' : '发送订单中所有还没发货的商品' }}</div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="shipVisible = false">取消</el-button><el-button type="primary" :loading="shipping" @click="doShip">确认{{ sf.compensation ? '补发' : '发货' }}</el-button></template>
    </el-dialog>

    <!-- 采购 -->
    <el-dialog v-model="purVisible" :title="`采购 · ${purRow?.order_id || ''}`" width="600px">
      <el-form label-width="110px">
        <el-form-item label="采购单号"><el-input v-model="pf.purchase_order_no" placeholder="如 1688 订单号" clearable /></el-form-item>
        <el-form-item label="采购实付（元）">
          <el-input-number v-model="pf.purchase_cost" :min="0" :precision="2" :step="1" placeholder="含采购运费" style="width: 180px" />
          <span class="muted small ml">含运费、扣除优惠后实际付的钱；填写后毛利按实际计算</span>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="pf.purchase_note" placeholder="可选" /></el-form-item>
        <el-form-item v-if="pf.lines.length" label="货源单价（元）">
          <div style="width: 100%">
            <div v-for="l in pf.lines" :key="l.source_id" class="pline">
              <span class="ellipsis" :title="l.title">{{ l.title }}</span>
              <el-input-number v-model="l.unit_price" :min="0.01" :precision="2" :step="0.1" size="small" style="width: 130px" />
            </div>
            <div class="muted small">改了单价会同时更新这个商品的货源价（保留 1688 原价），以后的订单按新价格预估毛利。</div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="purVisible = false">取消</el-button><el-button type="primary" :loading="purSaving" @click="savePurchase">保存</el-button></template>
    </el-dialog>

    <!-- 批量发货 -->
    <el-dialog v-model="batchVisible" title="批量发货" width="640px">
      <p class="muted small">每行一个订单：<b>订单号　快递公司　运单号</b>（空格、Tab 或逗号分隔，可以直接从 Excel 复制三列粘贴）。快递公司可以写名称（如 中通）或编码（如 ZTO）。</p>
      <el-input v-model="batchText" type="textarea" :rows="10" placeholder="3740615526570443264 中通 78812345678901" />
      <el-table v-if="batchResult" :data="batchResult.results.filter((r: any) => !r.ok)" size="small" max-height="220" style="margin-top: 8px">
        <el-table-column prop="line" label="行" show-overflow-tooltip />
        <el-table-column prop="error" label="失败原因" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <span v-if="batchResult" class="muted" style="margin-right: 12px">成功 {{ batchResult.ok }} 个，失败 {{ batchResult.failed }} 个</span>
        <el-button @click="batchVisible = false">关闭</el-button>
        <el-button type="primary" :loading="batching" @click="doBatch">发货</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rawVisible" :title="`订单原始数据 ${rawRow?.order_id || ''}`" width="760px">
      <el-button size="small" @click="copyRaw">复制</el-button>
      <pre class="raw">{{ rawText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { http, yuan } from '../api'

const TABS = [
  { key: 'all', label: '全部' }, { key: '20', label: '待发货' }, { key: '21', label: '部分发货' },
  { key: '30', label: '待收货' }, { key: '100', label: '已完成' }, { key: '10', label: '待付款' }, { key: '250', label: '已取消' },
]
const statusType = (s: number) => (s === 20 || s === 21 ? 'danger' : s === 30 ? 'warning' : s === 100 ? 'success' : 'info')

const stores = ref<any[]>([])
const items = ref<any[]>([])
const total = ref(0)
const counts = ref<Record<string, number>>({})
const loading = ref(false)
const statusTab = ref('all')
const dates = ref<string[] | null>(null)
const q = reactive({ page: 1, size: 20, store_id: undefined as number | undefined, keyword: '' })

async function loadStores() { stores.value = (await http.get('/orders/stores')).data }
async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/orders', { params: {
      page: q.page, size: q.size, store_id: q.store_id, keyword: q.keyword || undefined,
      status: statusTab.value === 'all' ? undefined : Number(statusTab.value),
      date_from: dates.value?.[0], date_to: dates.value?.[1] } })
    items.value = data.items; total.value = data.total; counts.value = data.status_counts
  } finally { loading.value = false }
}
function search() { q.page = 1; load() }

const syncing = ref(false)
let timer: number | undefined
async function sync(ids: number[]) {
  syncing.value = true
  try {
    const { data } = await http.post('/orders/sync', { store_ids: ids })
    ElMessage.success(data.started.length ? `已开始同步 ${data.started.length} 个店铺的订单` : '正在同步中')
    await loadStores(); poll()
  } finally { syncing.value = false }
}
function poll() {
  clearInterval(timer)
  timer = window.setInterval(async () => {
    await loadStores()
    if (!stores.value.some((s) => s.sync_status === 'running')) { clearInterval(timer); load() }
  }, 3000)
}

// 买家信息: decoded once per order through the WeChat API, then kept on the order
async function reveal(row: any, again = false) {
  row._revealing = true
  try { Object.assign(row, (await http.post(`/orders/${row.id}/reveal`, undefined, { params: again ? { again: true } : {} })).data) } finally { row._revealing = false }
}
async function delayVirtual(row: any) {
  Object.assign(row, (await http.post(`/orders/${row.id}/virtual-delay`)).data)
  ElMessage.success(`虚拟号已延期${row.virtual_phone_expire ? `，${row.virtual_phone_expire.slice(0, 10)} 到期` : ''}`)
}
async function applyReal(row: any) {
  await ElMessageBox.confirm('向微信申请查看这个订单买家的真实手机号？需要微信审核，通过后点「查询审核」即可显示真实号码。', '申请真实号码', { type: 'info' })
  await http.post(`/orders/${row.id}/real-number`)
  row._applied = true
  ElMessage.success('已提交申请，审核通过后点「查询审核」')
}
async function checkReal(row: any) {
  const { data } = await http.get(`/orders/${row.id}/real-number`)
  Object.assign(row, data.order)
  if (!row.phone_masked) ElMessage.success('已显示真实号码')
  else ElMessageBox.alert(`<pre style="white-space: pre-wrap">${JSON.stringify(data.response, null, 2)}</pre>`, '审核状态', { dangerouslyUseHTMLString: true })
}

// 发货
const companies = ref<any[]>([])
async function loadCompanies() { if (!companies.value.length) companies.value = (await http.get('/orders/delivery-companies')).data }
const companyName = (id: string) => companies.value.find((c) => c.id === id)?.name || id
const shipVisible = ref(false)
const shipping = ref(false)
const shipRow = ref<any>(null)
const sf = reactive<any>({ delivery_id: '', waybill_id: '', deliver_type: 1, compensation: false, reason: '' })
async function openShip(row: any, compensation = false) {
  shipRow.value = row
  Object.assign(sf, { delivery_id: sf.delivery_id, waybill_id: '', deliver_type: 1, compensation, reason: '' })
  shipVisible.value = true
  await loadCompanies()
}
async function doShip() {
  shipping.value = true
  try {
    const { data } = await http.post(`/orders/${shipRow.value.id}/ship`, sf)
    Object.assign(shipRow.value, data)
    shipVisible.value = false
    ElMessage.success(sf.compensation ? '已补发货' : '已发货')
  } finally { shipping.value = false }
}
// 采购
const purVisible = ref(false)
const purSaving = ref(false)
const purRow = ref<any>(null)
const pf = reactive<any>({ purchase_order_no: '', purchase_cost: undefined, purchase_note: '', lines: [] })
function openPurchase(row: any) {
  purRow.value = row
  const seen = new Set()
  const lines = row.items.filter((it: any) => it.source && !seen.has(it.source.id) && seen.add(it.source.id))
    .map((it: any) => ({ source_id: it.source.id, title: it.title, unit_price: it.source.price / 100, old: it.source.price }))
  Object.assign(pf, { purchase_order_no: row.purchase_order_no, purchase_note: row.purchase_note, lines,
    purchase_cost: row.purchase_cost != null ? row.purchase_cost / 100 : undefined })
  purVisible.value = true
}
async function savePurchase() {
  purSaving.value = true
  try {
    const { data } = await http.put(`/orders/${purRow.value.id}/purchase`, {
      purchase_order_no: pf.purchase_order_no || '', purchase_note: pf.purchase_note || '',
      purchase_cost: pf.purchase_cost ?? null,
      source_prices: pf.lines.filter((l: any) => Math.round(l.unit_price * 100) !== l.old).map((l: any) => ({ source_id: l.source_id, unit_price: l.unit_price })) })
    Object.assign(purRow.value, data)
    purVisible.value = false
    ElMessage.success('已保存')
    if (pf.lines.some((l: any) => Math.round(l.unit_price * 100) !== l.old)) load()  // other orders of the product
  } finally { purSaving.value = false }
}
const router = useRouter()
function goProduct(row: any, it: any) { router.push({ path: '/products', query: { keyword: it.product_id, store_id: row.store_id } }) }

const batchVisible = ref(false)
const batchText = ref('')
const batching = ref(false)
const batchResult = ref<any>(null)
function openBatch() { batchResult.value = null; batchVisible.value = true; loadCompanies() }
async function doBatch() {
  if (!batchText.value.trim()) return ElMessage.warning('请粘贴要发货的订单')
  batching.value = true
  try {
    batchResult.value = (await http.post('/orders/ship-batch', { text: batchText.value, store_id: q.store_id })).data
    if (!batchResult.value.failed) { ElMessage.success(`已发货 ${batchResult.value.ok} 个订单`); batchVisible.value = false }
    load()
  } finally { batching.value = false }
}
const revealingAll = ref(false)
const revealDone = ref(0)
const revealTotal = ref(0)
async function revealPage() {
  const rows = items.value.filter((o) => o.masked)
  revealingAll.value = true; revealDone.value = 0; revealTotal.value = rows.length
  let failed = 0
  try {
    for (const row of rows) {
      try { Object.assign(row, (await http.post(`/orders/${row.id}/reveal`)).data) } catch { failed++ }
      revealDone.value++
    }
  } finally { revealingAll.value = false }
  failed ? ElMessage.warning(`${failed} 个订单获取失败，可单独点「显示买家信息」查看原因`) : ElMessage.success(`已显示 ${rows.length} 个订单的买家信息`)
}
async function copyBuyer(row: any) {
  const phone = row.virtual_phone && row.phone_masked ? row.virtual_phone : row.receiver_phone
  const text = `${row.receiver_name} ${phone} ${row.receiver_address}`
  try { await navigator.clipboard.writeText(text); ElMessage.success('已复制收件信息') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

const rawVisible = ref(false)
const rawRow = ref<any>(null)
const rawText = ref('')
async function showRaw(row: any) {
  rawRow.value = row; rawText.value = '加载中…'; rawVisible.value = true
  const { data } = await http.get(`/orders/${row.id}`)
  rawText.value = JSON.stringify(data.decoded ? { 订单详情: data.raw, 解码买家信息: data.decoded } : data.raw, null, 2)
}
async function copyRaw() {
  try { await navigator.clipboard.writeText(rawText.value); ElMessage.success('已复制') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

onMounted(async () => { await loadStores(); load(); loadCompanies().catch(() => {}); if (stores.value.some((s) => s.sync_status === 'running')) poll() })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.stores { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 8px; }
.scard { border: 1px solid #eef0f4; border-radius: 8px; padding: 12px; cursor: pointer; background: #fafbfc; }
.scard.active { border-color: #409eff; background: #ecf5ff; }
.b { font-weight: 600; }
.small { font-size: 12px; }
.warn { color: #e6a23c; }
.danger { color: #f56c6c; }
.ok { color: #67c23a; }
.line { display: flex; gap: 10px; padding: 4px 0; }
.line + .line { border-top: 1px dashed #eef0f4; }
.thumb { width: 52px; height: 52px; border-radius: 4px; flex-shrink: 0; }
.info { min-width: 0; flex: 1; }
.ml { margin-left: 6px; }
.pline { display: flex; align-items: center; gap: 8px; justify-content: space-between; margin-bottom: 6px; }
.pline .ellipsis { flex: 1; min-width: 0; }
.raw { max-height: 60vh; overflow: auto; background: #f6f8fa; padding: 10px; border-radius: 6px; font-size: 12px; }
</style>

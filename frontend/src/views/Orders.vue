<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">订单管理 <span class="muted">（从微信小店同步；采购、发货功能在此基础上开发）</span></div>
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
                <div class="ellipsis" :title="it.title">{{ it.title }}</div>
                <div class="muted small">{{ it.attrs || '-' }} · ×{{ it.qty }} · ¥{{ yuan(it.real_price || it.sale_price) }}</div>
                <div class="small">
                  <template v-if="it.source">
                    货源：<a :href="it.source.detail_url" target="_blank" :title="it.source.supplier">¥{{ yuan(it.source.price) }} {{ it.source.supplier || '1688' }}</a>
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
            <template v-if="row.source_cost != null">
              <div class="muted small">货源 ¥{{ yuan(row.source_cost) }}</div>
              <div class="small" :class="row.margin >= 0 ? 'ok' : 'danger'">毛利 ¥{{ yuan(row.margin) }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="收件人" min-width="220">
          <template #default="{ row }">
            <div>{{ row.receiver_name }} {{ row.receiver_phone }}</div>
            <div class="muted small">{{ row.receiver_address }}</div>
            <el-button v-if="row.masked" link type="primary" size="small" :loading="row._revealing" @click="reveal(row)">显示买家信息</el-button>
            <el-button v-else-if="row.receiver_name" link type="primary" size="small" @click="copyBuyer(row)">复制</el-button>
            <div v-if="row.buyer_note" class="warn small">买家留言：{{ row.buyer_note }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }"><el-button link type="primary" @click="showRaw(row)">原始数据</el-button></template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]"
                       layout="total, sizes, prev, pager, next" @change="load" />
      </div>
      <p class="muted small">毛利 = 实付 − 货源价 × 数量（不含采购运费），只在每个商品都设置了货源时显示。</p>
      <p class="muted small">微信同步的订单中，买家姓名、电话、地址是打码的（如 郑**）。点「显示买家信息」通过微信接口获取完整信息并保存在订单里，之后不会重复获取（该接口每天有调用次数限制）。买家信息仅用于发货，请勿外传。</p>
    </div>

    <el-dialog v-model="rawVisible" :title="`订单原始数据 ${rawRow?.order_id || ''}`" width="760px">
      <el-button size="small" @click="copyRaw">复制</el-button>
      <pre class="raw">{{ rawText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
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
async function reveal(row: any) {
  row._revealing = true
  try { Object.assign(row, (await http.post(`/orders/${row.id}/reveal`)).data) } finally { row._revealing = false }
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
  const text = `${row.receiver_name} ${row.receiver_phone} ${row.receiver_address}`
  try { await navigator.clipboard.writeText(text); ElMessage.success('已复制收件信息') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

const rawVisible = ref(false)
const rawRow = ref<any>(null)
const rawText = ref('')
async function showRaw(row: any) {
  rawRow.value = row; rawText.value = '加载中…'; rawVisible.value = true
  rawText.value = JSON.stringify((await http.get(`/orders/${row.id}`)).data.raw, null, 2)
}
async function copyRaw() {
  try { await navigator.clipboard.writeText(rawText.value); ElMessage.success('已复制') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

onMounted(async () => { await loadStores(); load(); if (stores.value.some((s) => s.sync_status === 'running')) poll() })
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
.raw { max-height: 60vh; overflow: auto; background: #f6f8fa; padding: 10px; border-radius: 6px; font-size: 12px; }
</style>

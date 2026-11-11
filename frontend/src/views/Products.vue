<template>
  <div class="wrap">
    <!-- stores -->
    <div class="card stores">
      <div class="card-head"><div class="card-title">店铺</div>
        <el-button size="small" type="primary" :disabled="!stores.length" @click="sync(stores.map((s) => s.id))">全部同步</el-button>
      </div>
      <div class="store" :class="{ active: storeId === null }" @click="pick(null)">
        <div class="b">全部店铺</div><div class="muted">{{ stores.reduce((a, s) => a + s.count, 0) }} 个商品</div>
      </div>
      <div v-for="s in stores" :key="s.id" class="store" :class="{ active: storeId === s.id }" @click="pick(s.id)">
        <div class="row">
          <el-avatar :src="s.logo" :size="26">{{ s.name.slice(0, 1) }}</el-avatar>
          <div class="grow"><div class="b ellipsis">{{ s.name }}</div>
            <div class="muted">{{ s.count }} 个 · 在售 {{ s.on_sale }}</div></div>
          <el-button link type="primary" size="small" :loading="s.sync_status === 'running'" @click.stop="sync([s.id])">同步</el-button>
        </div>
        <div class="muted small" :class="{ danger: s.sync_status === 'failed' }">
          {{ s.sync_status === 'running' ? s.sync_message : s.sync_status === 'failed' ? s.sync_message : s.synced_at ? `上次同步 ${s.synced_at}` : '未同步' }}
        </div>
      </div>
    </div>

    <!-- products -->
    <div class="main">
      <div class="card">
        <el-form :inline="true">
          <el-form-item label="搜索"><el-input v-model="q.keyword" placeholder="标题 / 商品ID / 商品编码" clearable style="width: 240px" @keyup.enter="search" /></el-form-item>
          <el-form-item label="状态">
            <el-select v-model="q.status" placeholder="全部" clearable style="width: 140px" @change="search">
              <el-option label="上架" :value="5" /><el-option label="已下架" :value="11" /><el-option label="草稿" :value="0" />
              <el-option label="违规下架" :value="13" />
            </el-select>
          </el-form-item>
          <el-form-item label="货源">
            <el-select v-model="q.source" placeholder="全部" clearable style="width: 130px" @change="search">
              <el-option label="已设货源" value="linked" /><el-option label="无货源" value="none" /><el-option label="未找货源" value="unsearched" />
            </el-select>
          </el-form-item>
          <el-form-item><el-button type="primary" icon="Search" @click="search">查询</el-button></el-form-item>
        </el-form>
      </div>
      <div class="card">
        <div class="card-head">
          <div class="card-title">商品列表 <span class="muted">（共 {{ total }} 个）</span></div>
          <div>
            <el-tooltip :content="selected.length ? `用选中的 ${selected.length} 个商品找货源` : '自动选取列表中接下来 10 个未找过货源的商品'" placement="top">
              <el-button type="success" plain :loading="picking" @click="findSource">找货源{{ selected.length ? `（${selected.length}）` : '' }}</el-button>
            </el-tooltip>
            <el-button :disabled="!selected.length" @click="batchVisible = true">批量改价</el-button>
            <el-button :disabled="!selected.length" @click="act('listing')">批量上架</el-button>
            <el-button :disabled="!selected.length" @click="act('delisting')">批量下架</el-button>
            <el-button type="danger" plain :disabled="!selected.length" @click="act('delete')">批量删除</el-button>
            <el-button type="danger" :disabled="!noSourceCount" :loading="purging" @click="deleteNoSource">删除全部无货源商品（{{ noSourceCount }}）</el-button>
          </div>
        </div>
        <el-table :data="items" v-loading="loading" border @selection-change="(v: any[]) => (selected = v)">
          <el-table-column type="selection" width="45" align="center" />
          <el-table-column label="主图" width="90" align="center">
            <template #default="{ row }"><el-image :src="row.cover" fit="cover" style="width: 64px; height: 64px; border-radius: 4px" :preview-src-list="[row.cover]" preview-teleported lazy /></template>
          </el-table-column>
          <el-table-column label="商品" min-width="240">
            <template #default="{ row }">
              <div class="two" :title="row.title">{{ row.title }}</div>
              <div class="muted small">{{ row.store_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="商品ID / 编码" min-width="170">
            <template #default="{ row }"><div>{{ row.wx_product_id }}</div><div class="muted small">{{ row.spu_code || '-' }}</div></template>
          </el-table-column>
          <el-table-column label="零售价" width="150" align="right"><template #default="{ row }">{{ priceRange(row.price_min, row.price_max) }}</template></el-table-column>
          <el-table-column label="货源价" width="110" align="center">
            <template #default="{ row }">
              <a v-if="row.source" :href="row.source.detail_url" target="_blank" :title="row.source.supplier">¥{{ yuan(row.source.price) }}</a>
              <el-tooltip v-else-if="row.source_state === 'none'" content="已找过货源，但未设为货源" placement="top"><el-tag type="danger" size="small">无货源</el-tag></el-tooltip>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="stock" label="库存" width="80" align="right" />
          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }"><el-tag :type="row.status === 5 ? 'success' : row.status === 11 ? 'info' : 'warning'">{{ row.status_name }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="200" align="center" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openPrice(row)">改价</el-button>
              <el-button v-if="row.status === 5" link type="primary" @click="act('delisting', [row])">下架</el-button>
              <el-button v-else link type="primary" @click="act('listing', [row])">上架</el-button>
              <el-button link type="danger" @click="act('delete', [row])">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager"><el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @change="load" /></div>
      </div>
    </div>

    <!-- single product price (per SKU) -->
    <el-dialog v-model="priceVisible" :title="`改价：${editing?.title || ''}`" width="640px">
      <el-table v-if="editing" :data="editing.skus" size="small" border>
        <el-table-column label="规格" prop="attrs" min-width="160" />
        <el-table-column label="SKU 编码" prop="sku_code" width="120" />
        <el-table-column label="当前价" width="90" align="right"><template #default="{ row }">¥{{ yuan(row.price) }}</template></el-table-column>
        <el-table-column label="新价格（元）" width="150">
          <template #default="{ row }"><el-input-number v-model="row._new" :min="0.01" :precision="2" :step="1" size="small" controls-position="right" style="width: 120px" /></template>
        </el-table-column>
      </el-table>
      <p class="muted">通过微信「免审更新商品」接口直接改价，已上架商品无需重新审核。</p>
      <template #footer><el-button @click="priceVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="savePrice">保存</el-button></template>
    </el-dialog>

    <!-- batch price -->
    <el-dialog v-model="batchVisible" :title="`批量改价（${selected.length} 个商品，所有规格）`" width="460px">
      <el-radio-group v-model="batch.mode">
        <el-radio value="multiply">按比例</el-radio><el-radio value="add">加减金额</el-radio><el-radio value="set">统一价格</el-radio>
      </el-radio-group>
      <div style="margin-top: 14px">
        <span class="muted">{{ batch.mode === 'multiply' ? '现价 ×' : batch.mode === 'add' ? '现价 +（负数为减）' : '统一设为' }}</span>
        <el-input-number v-model="batch.value" :precision="2" :step="batch.mode === 'multiply' ? 0.05 : 1" style="margin-left: 8px" />
        <span class="muted">{{ batch.mode === 'multiply' ? '倍' : '元' }}</span>
      </div>
      <template #footer><el-button @click="batchVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveBatch">确定</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { http, priceRange, yuan } from '../api'

const stores = ref<any[]>([])
const storeId = ref<number | null>(null)
const q = reactive<any>({ keyword: '', status: undefined, source: '', page: 1, size: 20 })
const items = ref<any[]>([])
const total = ref(0)
const noSourceCount = ref(0)
const loading = ref(false)
const selected = ref<any[]>([])
const saving = ref(false)

async function loadStores() { stores.value = (await http.get('/products/stores')).data }
function filters() {
  const params: any = { keyword: q.keyword, status: q.status, store_id: storeId.value ?? undefined }
  if (params.status === undefined || params.status === '') delete params.status
  return params
}
async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/products', { params: { ...filters(), source: q.source || undefined, page: q.page, size: q.size } })
    items.value = data.items; total.value = data.total; noSourceCount.value = data.no_source_count
  } finally { loading.value = false }
}
function search() { q.page = 1; load() }
function pick(id: number | null) { storeId.value = id; search() }

async function sync(ids: number[]) {
  await http.post('/products/sync', { store_ids: ids })
  ElMessage.success('已开始同步，商品较多时需要几分钟')
  loadStores()
}

function summarize(res: any[], what: string) {
  const bad = res.filter((r) => !r.ok)
  if (!bad.length) ElMessage.success(`${what}成功 ${res.length} 个`)
  else ElMessageBox.alert(bad.map((b) => `商品 ${items.value.find((i) => i.id === b.id)?.wx_product_id || b.id}：${b.error}`).join('<br>'),
    `${what}：成功 ${res.length - bad.length} 个，失败 ${bad.length} 个`, { dangerouslyUseHTMLString: true })
}

async function act(action: string, rows = selected.value) {
  const name = { listing: '上架', delisting: '下架', delete: '删除' }[action] as string
  if (action === 'delete') await ElMessageBox.confirm(`在微信小店中删除这 ${rows.length} 个商品？在售商品会先下架。删除后无法恢复。`, '确认删除', { type: 'warning' })
  const { data } = await http.post('/products/batch-action', { ids: rows.map((r) => r.id), action })
  summarize(data.results, name); load(); loadStores()
}

const priceVisible = ref(false)
const editing = ref<any>(null)
function openPrice(row: any) {
  editing.value = { ...row, skus: row.skus.map((s: any) => ({ ...s, _new: s.price / 100 })) }
  priceVisible.value = true
}
async function savePrice() {
  const changed = editing.value.skus.filter((s: any) => Math.round(s._new * 100) !== s.price)
  if (!changed.length) return (priceVisible.value = false)
  saving.value = true
  try {
    await http.post(`/products/${editing.value.id}/price`, { skus: changed.map((s: any) => ({ sku_id: s.sku_id, price: s._new })) })
    ElMessage.success('已改价'); priceVisible.value = false; load()
  } finally { saving.value = false }
}

const batchVisible = ref(false)
const batch = reactive({ mode: 'multiply', value: 1.1 })
async function saveBatch() {
  saving.value = true
  try {
    const { data } = await http.post('/products/batch-price', { ids: selected.value.map((r) => r.id), ...batch })
    batchVisible.value = false; summarize(data.results, '改价'); load()
  } finally { saving.value = false }
}

const router = useRouter()
const picking = ref(false)
async function findSource() {
  let rows: any[] = selected.value
  if (rows.length > 10) return ElMessage.warning('一次最多选择 10 个商品找货源')
  if (!rows.length) {
    // No selection: take the next 10 products (list order) that have no source and were not searched yet
    picking.value = true
    try { rows = (await http.get('/products/next-batch', { params: { ...filters(), limit: 10 } })).data }
    finally { picking.value = false }
    if (!rows.length) return ElMessage.info('当前列表中的商品都已找过货源')
  }
  try {
    sessionStorage.setItem('sourcing_filters', JSON.stringify(filters())) // used by 继续下一批
    sessionStorage.setItem('sourcing_selection', JSON.stringify(
      rows.map((r) => ({ kind: 'sp', id: r.id, title: r.title, image_url: r.cover }))))
  } catch { /* storage unavailable: the page still works from the ids */ }
  router.push({ path: '/sourcing', query: { sp: rows.map((r) => r.id).join(',') } })
}

const purging = ref(false)
async function deleteNoSource() {
  const where = storeId.value ? `「${stores.value.find((s) => s.id === storeId.value)?.name}」中` : '所有店铺中'
  await ElMessageBox.confirm(
    `删除${where}全部 ${noSourceCount.value} 个「无货源」商品（已找过货源但未设为货源）？<br>会同时在微信小店中删除，在售商品会先下架。删除后无法恢复。`,
    '删除全部无货源商品', { type: 'warning', dangerouslyUseHTMLString: true, confirmButtonText: '确认删除' })
  purging.value = true
  try {
    const ids: number[] = (await http.get('/products/no-source-ids', { params: filters() })).data
    const results: any[] = []
    for (let i = 0; i < ids.length; i += 50) {
      const { data } = await http.post('/products/batch-action', { ids: ids.slice(i, i + 50), action: 'delete' })
      results.push(...data.results)
    }
    if (results.length) summarize(results, '删除')
  } finally { purging.value = false; load(); loadStores() }
}

let timer: number | undefined
onMounted(() => {
  loadStores(); load()
  timer = window.setInterval(async () => {
    if (stores.value.some((s) => s.sync_status === 'running')) {
      await loadStores()
      if (!stores.value.some((s) => s.sync_status === 'running')) load()
    }
  }, 3000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.wrap { display: flex; gap: 14px; align-items: flex-start; }
.stores { width: 260px; flex-shrink: 0; }
.main { flex: 1; min-width: 0; }
.store { border: 1px solid #eef0f4; border-radius: 8px; padding: 10px; margin-bottom: 8px; cursor: pointer; }
.store.active { border-color: #409eff; background: #ecf5ff; }
.row { display: flex; align-items: center; gap: 8px; }
.grow { flex: 1; min-width: 0; }
.b { font-weight: 600; }
.small { font-size: 12px; margin-top: 4px; }
.danger { color: #f56c6c; }
.two { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>

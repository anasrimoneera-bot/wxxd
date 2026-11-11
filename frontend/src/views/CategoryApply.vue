<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">一键申请类目 <span class="muted">（读取微信小店全部类目及保证金，按保证金勾选后，一键申请店铺还没开通的类目）</span></div>
      </div>
      <el-form :inline="true">
        <el-form-item label="店铺">
          <el-select v-model="storeId" placeholder="选择店铺" style="width: 200px" @change="load">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" plain :loading="syncing" :disabled="!storeId" @click="startSync(false)">同步类目库</el-button>
          <el-button :disabled="!storeId || syncing" @click="startSync(true)">重新读取全部保证金</el-button>
        </el-form-item>
        <el-form-item>
          <span class="muted small">
            <template v-if="sync.status === 'running'">同步中：{{ sync.message }} {{ sync.total ? `${sync.done} / ${sync.total}` : '' }}</template>
            <template v-else-if="sync.leaves">类目库：{{ sync.leaves }} 个叶子类目，已读取保证金 {{ sync.with_deposit }} 个{{ sync.finished_at ? `，更新于 ${sync.finished_at}` : '' }}</template>
            <template v-else>还没有同步类目库，请先点「同步类目库」（数千个类目，第一次需要几分钟到十几分钟）</template>
          </span>
          <span v-if="sync.status === 'failed'" class="danger small">　{{ sync.message }}</span>
        </el-form-item>
      </el-form>
      <el-progress v-if="sync.status === 'running' && sync.total" :percentage="Math.round(sync.done * 100 / sync.total)" style="margin-bottom: 8px" />
      <el-alert v-if="openedError" type="warning" :title="openedError" :closable="false" show-icon style="margin-bottom: 8px" />

      <div class="tiers">
        <div class="label">保证金（微信返回的金额）：</div>
        <el-checkbox-group v-model="pickedTiers">
          <el-checkbox v-for="t in tiers" :key="t.key" :value="t.key">{{ t.label }}<span class="muted">（{{ t.count }} 个）</span></el-checkbox>
        </el-checkbox-group>
        <div class="quick">
          <el-button link type="primary" size="small" @click="pickUpTo(0)">只选无需保证金</el-button>
          <el-button link type="primary" size="small" @click="pickUpTo(1000)">1000 元及以下</el-button>
          <el-button link type="primary" size="small" @click="pickUpTo(5000)">5000 元及以下</el-button>
          <el-button link type="primary" size="small" @click="pickedTiers = tiers.map((t) => t.key)">全选</el-button>
          <el-button link size="small" @click="pickedTiers = []">清空</el-button>
        </div>
      </div>
      <div class="filters">
        <el-checkbox v-model="skipQua">跳过需要资质的类目</el-checkbox>
        <el-checkbox v-model="showOpened">显示已开通</el-checkbox>
        <el-checkbox v-model="hideApplied">隐藏已提交申请的</el-checkbox>
        <el-input v-model="keyword" placeholder="搜索类目名称" clearable size="small" style="width: 200px; margin-left: 12px" />
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">类目 <span class="muted">（店铺已开通 {{ openedCount }} 个 · 未开通 {{ notOpened.length }} 个 · 符合条件 {{ eligible.length }} 个）</span></div>
        <div>
          <span v-if="applying" class="muted small" style="margin-right: 8px">正在申请 {{ applyDone }} / {{ applyTotal }}</span>
          <el-button :disabled="!selected.length || applying" @click="apply(selected)">申请所选（{{ selected.length }}）</el-button>
          <el-button type="primary" :disabled="!eligible.length || applying" :loading="applying" @click="apply(eligible)">一键申请符合条件的（{{ eligible.length }}）</el-button>
        </div>
      </div>
      <el-table :data="pageRows" v-loading="loading" border @selection-change="(v: any[]) => (selected = v)">
        <el-table-column type="selection" width="45" align="center" :selectable="(r: any) => !r.opened" />
        <el-table-column label="类目" min-width="260">
          <template #default="{ row }">
            <div>{{ row.name }} <span class="muted small">#{{ row.cat_id }}</span></div>
            <div class="muted small">{{ row.path_names }}</div>
          </template>
        </el-table-column>
        <el-table-column label="保证金" width="130" align="right">
          <template #default="{ row }">
            <span v-if="row.deposit === null || row.deposit === undefined" class="muted">未知</span>
            <span v-else-if="row.deposit === 0">无需保证金</span>
            <span v-else>¥{{ (row.deposit / 100).toLocaleString() }}</span>
            <div><el-button link size="small" @click="showRaw(row)">原始数据</el-button></div>
          </template>
        </el-table-column>
        <el-table-column label="资质" width="110" align="center">
          <template #default="{ row }">
            <el-tooltip v-if="row.needs_qualification" :content="row.qua_tips || '需要上传资质'" placement="top"><el-tag type="warning" size="small">需要资质</el-tag></el-tooltip>
            <span v-else class="muted small">不需要</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="220">
          <template #default="{ row }">
            <el-tag v-if="row.opened" type="success" size="small">已开通</el-tag>
            <template v-else-if="row.application">
              <el-tag :type="row.application.ok ? 'primary' : 'danger'" size="small">{{ row.application.ok ? '已提交申请' : '申请失败' }}</el-tag>
              <div class="muted small">{{ row.application.ok ? `申请单 ${row.application.audit_id || '-'} · ${row.application.created_at}` : row.application.message }}</div>
            </template>
            <el-tag v-else type="info" size="small">未开通</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="shown.length" :page-sizes="[50, 100, 200]" layout="total, sizes, prev, pager, next" /></div>
      <p class="muted small">
        保证金来自微信「获取保证金类目规则」接口（单位分；读取不到时用「获取类目信息」里的保证金）。如果金额和微信小店后台不一致，点「原始数据」把内容发给我们核对。
        申请时不上传资质材料，需要资质的类目通常会被拒绝，请到微信小店后台手动申请。提交后由微信审核。
      </p>
    </div>

    <el-dialog v-model="rawVisible" :title="`接口原始数据：${rawRow?.name || ''} #${rawRow?.cat_id || ''}`" width="720px">
      <div v-loading="rawLoading">
        <el-button size="small" @click="copyRaw">复制</el-button>
        <pre class="raw">{{ rawText }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http } from '../api'

const stores = ref<any[]>([])
const storeId = ref<number>()
const rows = ref<any[]>([])
const sync = ref<any>({})
const openedError = ref('')
const openedCount = ref(0)
const loading = ref(false)
const syncing = computed(() => sync.value.status === 'running')
const pickedTiers = ref<string[]>([])
const skipQua = ref(true)
const showOpened = ref(false)
const hideApplied = ref(true)
const keyword = ref('')
const selected = ref<any[]>([])
const page = ref(1)
const pageSize = ref(50)
const applying = ref(false)
const applyDone = ref(0)
const applyTotal = ref(0)

const tierKey = (r: any) => (r.deposit === null || r.deposit === undefined ? 'unknown' : String(r.deposit))
const notOpened = computed(() => rows.value.filter((r) => !r.opened))
const tiers = computed(() => {
  const counts = new Map<string, number>()
  for (const r of notOpened.value) counts.set(tierKey(r), (counts.get(tierKey(r)) || 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => (a[0] === 'unknown' ? 1 : b[0] === 'unknown' ? -1 : Number(a[0]) - Number(b[0])))
    .map(([key, count]) => ({ key, count, fen: key === 'unknown' ? null : Number(key),
      label: key === 'unknown' ? '未知' : key === '0' ? '无需保证金' : `¥${(Number(key) / 100).toLocaleString()}` }))
})
function pickUpTo(yuan: number) { pickedTiers.value = tiers.value.filter((t) => t.fen !== null && t.fen <= yuan * 100).map((t) => t.key) }

const matches = (r: any) => pickedTiers.value.includes(tierKey(r)) && !(skipQua.value && r.needs_qualification)
const shown = computed(() => {
  const k = keyword.value.trim()
  return rows.value.filter((r) => (showOpened.value || !r.opened) && (!hideApplied.value || !r.application?.ok) && (r.opened || matches(r))
    && (!k || r.name.includes(k) || r.path_names.includes(k)))
})
const eligible = computed(() => rows.value.filter((r) => !r.opened && !r.application?.ok && matches(r)))
const pageRows = computed(() => shown.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([pickedTiers, skipQua, showOpened, hideApplied, keyword], () => { page.value = 1 })

async function load() {
  if (!storeId.value) return
  loading.value = true
  try {
    const { data } = await http.get('/categories/overview', { params: { store_id: storeId.value } })
    const first = !rows.value.length
    rows.value = data.items; sync.value = data.sync; openedError.value = data.opened_error; openedCount.value = data.opened_count
    if (first && !pickedTiers.value.length) pickUpTo(5000)
  } finally { loading.value = false }
}

let timer: number | undefined
async function startSync(full: boolean) {
  if (full) await ElMessageBox.confirm('重新读取全部叶子类目的保证金？类目较多，需要十几分钟，可以离开这个页面。', '重新读取保证金', { type: 'info' })
  sync.value = (await http.post('/categories/catalog/sync', { store_id: storeId.value, full })).data
  poll()
}
function poll() {
  clearInterval(timer)
  timer = window.setInterval(async () => {
    sync.value = (await http.get('/categories/catalog/status')).data
    if (sync.value.status !== 'running') { clearInterval(timer); load() }
  }, 3000)
}

async function apply(list: any[]) {
  const items = list.filter((r) => !r.opened && !r.application?.ok)
  if (!items.length) return
  await ElMessageBox.confirm(`为店铺提交 ${items.length} 个类目的开通申请（不上传资质）？提交后由微信审核。`, '一键申请类目', { type: 'warning' })
  applying.value = true; applyDone.value = 0; applyTotal.value = items.length
  let ok = 0
  const failed: string[] = []
  try {
    for (let i = 0; i < items.length; i += 20) {
      const chunk = items.slice(i, i + 20)
      const { data } = await http.post('/categories/apply', { store_id: storeId.value, cat_ids: chunk.map((r) => r.cat_id) })
      for (const res of data.results) {
        const row = rows.value.find((r) => r.cat_id === res.cat_id)
        if (row) row.application = { ok: res.ok, audit_id: res.audit_id, message: res.message, created_at: '刚刚' }
        if (res.ok) ok++
        else failed.push(`${row?.name || res.cat_id}：${res.message}`)
      }
      applyDone.value += chunk.length
    }
  } finally { applying.value = false }
  if (!failed.length) ElMessage.success(`已提交 ${ok} 个类目申请，等待微信审核`)
  else ElMessageBox.alert(failed.slice(0, 50).join('<br>') + (failed.length > 50 ? `<br>…还有 ${failed.length - 50} 个` : ''),
    `提交成功 ${ok} 个，失败 ${failed.length} 个`, { dangerouslyUseHTMLString: true })
}

const rawVisible = ref(false)
const rawLoading = ref(false)
const rawRow = ref<any>(null)
const rawText = ref('')
async function showRaw(row: any) {
  rawRow.value = row; rawText.value = ''; rawVisible.value = true; rawLoading.value = true
  try {
    const { data } = await http.get('/categories/raw', { params: { store_id: storeId.value, cat_id: row.cat_id } })
    rawText.value = JSON.stringify(data, null, 2)
  } finally { rawLoading.value = false }
}
async function copyRaw() {
  try { await navigator.clipboard.writeText(rawText.value); ElMessage.success('已复制') } catch { ElMessage.warning('复制失败，请手动选择文字复制') }
}

onMounted(async () => {
  const s = await http.get('/stores')
  stores.value = s.data.items || s.data
  sync.value = (await http.get('/categories/catalog/status')).data
  if (sync.value.status === 'running') poll()
  if (stores.value.length === 1) { storeId.value = stores.value[0].id; load() }
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.small { font-size: 12px; }
.danger { color: #f56c6c; }
.tiers { display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap; margin: 4px 0 8px; }
.tiers .label { font-size: 14px; color: #606266; line-height: 32px; }
.quick { width: 100%; padding-left: 4px; }
.filters { display: flex; align-items: center; gap: 12px; }
.raw { max-height: 60vh; overflow: auto; background: #f6f8fa; padding: 10px; border-radius: 6px; font-size: 12px; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>

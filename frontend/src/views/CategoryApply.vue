<template>
  <div>
    <div class="card">
      <div class="card-head"><div class="card-title">一键申请类目 <span class="muted">（找出商品云库商品需要、但店铺还没开通的类目，按保证金筛选后一键申请）</span></div></div>
      <el-form :inline="true">
        <el-form-item label="店铺">
          <el-select v-model="storeId" placeholder="选择店铺" style="width: 200px" @change="scan">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="商品分组">
          <el-select v-model="groupId" placeholder="全部分组" clearable style="width: 160px" @change="scan">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="保证金">
          <el-select v-model="rangeKey" style="width: 150px">
            <el-option v-for="r in RANGES" :key="r.key" :label="r.label" :value="r.key" />
          </el-select>
          <template v-if="rangeKey === 'custom'">
            <el-input-number v-model="customMin" :min="0" :step="500" controls-position="right" style="width: 120px; margin-left: 6px" placeholder="最低" />
            <span style="margin: 0 4px">至</span>
            <el-input-number v-model="customMax" :min="0" :step="500" controls-position="right" style="width: 120px" placeholder="最高" />
            <span class="muted" style="margin-left: 4px">元</span>
          </template>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="skipQua">跳过需要资质的类目</el-checkbox>
          <el-checkbox v-model="includeUnknown">包含保证金未知的类目</el-checkbox>
          <el-checkbox v-model="showOpened">显示已开通</el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" icon="Search" :loading="loading" :disabled="!storeId" @click="scan">扫描类目</el-button>
        </el-form-item>
      </el-form>
      <el-alert v-if="openedError" type="warning" :title="openedError" :closable="false" show-icon style="margin-bottom: 8px" />
      <div v-if="detailTotal && detailDone < detailTotal" class="muted small">正在读取类目信息（保证金、资质）… {{ detailDone }} / {{ detailTotal }}</div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">类目 <span class="muted">（未开通 {{ notOpened.length }} 个 · 符合条件 {{ eligible.length }} 个）</span></div>
        <div>
          <el-button :disabled="!selected.length" :loading="applying" @click="apply(selected)">申请所选（{{ selected.length }}）</el-button>
          <el-button type="primary" :disabled="!eligible.length" :loading="applying" @click="apply(eligible)">一键申请符合条件的（{{ eligible.length }}）</el-button>
        </div>
      </div>
      <el-table :data="shown" v-loading="loading" border @selection-change="(v: any[]) => (selected = v)">
        <el-table-column type="selection" width="45" align="center" :selectable="(r: any) => !r.opened" />
        <el-table-column label="类目" min-width="200">
          <template #default="{ row }">
            <div>{{ row.info?.name || '（读取中）' }} <span class="muted small">#{{ row.cat_id }}</span></div>
            <div class="muted small">{{ row.path.join(' / ') }}</div>
          </template>
        </el-table-column>
        <el-table-column label="商品数" prop="products" width="80" align="center" />
        <el-table-column label="示例商品" prop="sample" min-width="200" show-overflow-tooltip />
        <el-table-column label="保证金" width="120" align="right">
          <template #default="{ row }">
            <span v-if="!row.info" class="muted">-</span>
            <span v-else-if="row.info.deposit === null || row.info.deposit === undefined" class="muted">未知</span>
            <span v-else-if="row.info.deposit === 0">无需保证金</span>
            <span v-else>¥{{ (row.info.deposit / 100).toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="资质" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.info?.needs_qualification" type="warning" size="small">需要资质</el-tag>
            <span v-else-if="row.info" class="muted small">不需要</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="200">
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
      <p class="muted small">
        说明：申请时不上传资质材料，需要资质的类目通常会被微信拒绝（请到微信小店后台手动申请）。保证金信息来自微信类目接口，缓存 7 天。
        提交后由微信审核，审核结果以微信小店后台为准；审核通过后，任务列表里因类目未开通失败的商品点「重试失败」即可。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http } from '../api'

const RANGES = [
  { key: 'all', label: '全部', min: null, max: null },
  { key: 'zero', label: '无需保证金', min: 0, max: 0 },
  { key: '1000', label: '1000 元以内', min: 0, max: 1000 },
  { key: '5000', label: '5000 元以内', min: 0, max: 5000 },
  { key: '1000-5000', label: '1000 – 5000 元', min: 1000, max: 5000 },
  { key: '5000-10000', label: '5000 – 10000 元', min: 5000, max: 10000 },
  { key: '10000+', label: '10000 元以上', min: 10000, max: null },
  { key: 'custom', label: '自定义', min: null, max: null },
] as const

const stores = ref<any[]>([])
const groups = ref<any[]>([])
const storeId = ref<number>()
const groupId = ref<number>()
const rangeKey = ref<string>('5000')
const customMin = ref<number | undefined>(0)
const customMax = ref<number | undefined>(5000)
const skipQua = ref(true)
const includeUnknown = ref(false)
const showOpened = ref(false)
const rows = ref<any[]>([])
const openedError = ref('')
const loading = ref(false)
const applying = ref(false)
const selected = ref<any[]>([])
const detailDone = ref(0)
const detailTotal = ref(0)

const range = computed(() => {
  const r = RANGES.find((x) => x.key === rangeKey.value)!
  return r.key === 'custom' ? { min: customMin.value ?? null, max: customMax.value ?? null } : { min: r.min, max: r.max }
})
function depositOk(row: any) {
  const d = row.info?.deposit
  if (d === null || d === undefined) return includeUnknown.value || (range.value.min === null && range.value.max === null)
  const yuan = d / 100
  if (range.value.min !== null && yuan < range.value.min) return false
  if (range.value.max !== null && yuan > range.value.max) return false
  return true
}
const notOpened = computed(() => rows.value.filter((r) => !r.opened))
const filtered = computed(() => rows.value.filter((r) => r.opened || (depositOk(r) && !(skipQua.value && r.info?.needs_qualification))))
const shown = computed(() => filtered.value.filter((r) => showOpened.value || !r.opened))
const eligible = computed(() => filtered.value.filter((r) => !r.opened && r.info && !(r.application?.ok)))

async function scan() {
  if (!storeId.value) return
  loading.value = true
  try {
    const { data } = await http.get('/categories/needed', { params: { store_id: storeId.value, group_id: groupId.value || undefined } })
    rows.value = data.items; openedError.value = data.opened_error
  } finally { loading.value = false }
  // fill in 保证金 / 资质 for the categories not cached yet, 20 at a time
  const missing = rows.value.filter((r) => !r.opened && !r.info).map((r) => r.cat_id)
  detailTotal.value = missing.length; detailDone.value = 0
  for (let i = 0; i < missing.length; i += 20) {
    const chunk = missing.slice(i, i + 20)
    try {
      const { data } = await http.post('/categories/details', { store_id: storeId.value, cat_ids: chunk })
      for (const r of rows.value) if (data[r.cat_id]) r.info = data[r.cat_id]
    } catch { /* the error is shown by the http helper; keep going */ }
    detailDone.value += chunk.length
  }
}

async function apply(list: any[]) {
  const items = list.filter((r) => !r.opened)
  if (!items.length) return
  await ElMessageBox.confirm(`为店铺提交 ${items.length} 个类目的开通申请（不上传资质）？`, '一键申请类目', { type: 'warning' })
  applying.value = true
  let ok = 0
  const failed: string[] = []
  try {
    for (let i = 0; i < items.length; i += 20) {
      const chunk = items.slice(i, i + 20)
      const { data } = await http.post('/categories/apply', { store_id: storeId.value, items: chunk.map((r) => ({ cat_id: r.cat_id, path: r.path })) })
      for (const res of data.results) {
        const row = rows.value.find((r) => r.cat_id === res.cat_id)
        if (row) row.application = { ok: res.ok, audit_id: res.audit_id, message: res.message, created_at: '刚刚' }
        if (res.ok) ok++
        else failed.push(`${row?.info?.name || res.cat_id}：${res.message}`)
      }
    }
  } finally { applying.value = false }
  if (!failed.length) ElMessage.success(`已提交 ${ok} 个类目申请，等待微信审核`)
  else ElMessageBox.alert(failed.join('<br>'), `提交成功 ${ok} 个，失败 ${failed.length} 个`, { dangerouslyUseHTMLString: true })
}

onMounted(async () => {
  const [s, g] = await Promise.all([http.get('/stores'), http.get('/library/groups')])
  stores.value = s.data.items || s.data
  groups.value = g.data
  if (stores.value.length === 1) { storeId.value = stores.value[0].id; scan() }
})
</script>

<style scoped>
.small { font-size: 12px; }
</style>

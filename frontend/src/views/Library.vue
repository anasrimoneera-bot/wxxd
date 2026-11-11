<template>
  <div>
    <div class="card">
      <div class="card-title" style="margin-bottom: 14px">数据源平台入口</div>
      <div class="platforms">
        <div v-for="p in platformStats" :key="p.key" class="pcard" :class="{ active: q.platform === p.key }"
             @click="q.platform = q.platform === p.key ? '' : p.key; search()">
          <span class="platform-icon" :style="{ background: p.color, width: '36px', height: '36px', fontSize: p.short.length > 1 ? '10px' : '16px' }">{{ p.short }}</span>
          <div><div class="pname">{{ p.name }}</div><div class="muted">{{ p.count }} 条数据</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <el-form :inline="true" class="filters" label-width="72px">
        <el-form-item label="商品名称"><el-input v-model="q.name" placeholder="请输入商品名称" clearable style="width: 220px" /></el-form-item>
        <el-form-item label="商品 ID"><el-input v-model="q.ids" type="textarea" :rows="1" placeholder="多个 ID 请用逗号、空格或换行分隔" style="width: 220px" /></el-form-item>
        <el-form-item label="来源平台">
          <el-select v-model="q.platform" placeholder="全部平台" clearable style="width: 160px">
            <el-option v-for="p in PLATFORMS" :key="p.key" :label="p.name" :value="p.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="分组">
          <el-select v-model="q.group_id" placeholder="全部分组" clearable style="width: 160px">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="价格区间">
          <el-input-number v-model="q.price_min" :min="0" placeholder="最低价" controls-position="right" style="width: 120px" />
          <span style="margin: 0 6px">至</span>
          <el-input-number v-model="q.price_max" :min="0" placeholder="最高价" controls-position="right" style="width: 120px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" icon="Search" @click="search">搜索</el-button>
          <el-button icon="Refresh" @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">商品云库</div>
        <div>
          <el-button icon="Download" @click="exportXlsx">导出表格</el-button>
          <el-button @click="groupVisible = true">分组管理</el-button>
          <el-button :disabled="!selected.length" @click="moveVisible = true">移动分组</el-button>
          <el-button type="warning" plain icon="MagicStick" :disabled="!selected.length" :loading="regenRunning"
                     @click="regenerate(selected.map((r) => r.id))">{{ regenRunning ? `生成中 ${regenDone}/${regenTotal}` : 'AI 生成标题' }}</el-button>
          <el-button type="danger" plain icon="Delete" :disabled="!selected.length" @click="openDelete(selected)">批量删除</el-button>
          <el-button type="primary" icon="Upload" @click="uploadVisible = true">上传商品数据包</el-button>
        </div>
      </div>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
        当前仅支持微信小店导出的 .json 或 .txt 商品数据包（也可打包成 .zip），单次最多上传 500 个；重复上传同一来源商品将更新云库数据。
      </el-alert>
      <el-table :data="items" v-loading="loading" border @selection-change="(v: any[]) => (selected = v)">
        <el-table-column type="selection" width="45" align="center" />
        <el-table-column label="封面" width="90" align="center">
          <template #default="{ row }"><el-image :src="row.cover" fit="cover" style="width: 60px; height: 60px; border-radius: 4px" lazy /></template>
        </el-table-column>
        <el-table-column label="商品名称（原标题）" min-width="220"><template #default="{ row }"><div class="ellipsis" :title="row.title">{{ row.title }}</div></template></el-table-column>
        <el-table-column label="新标题（发布时使用）" min-width="240">
          <template #default="{ row }">
            <div class="newtitle">
              <span v-if="row.ai_title" class="ellipsis" :title="row.ai_title">{{ row.ai_title }}</span>
              <span v-else class="muted">未生成（发布时自动生成）</span>
              <el-icon class="edit" title="手动修改" @click="editTitle(row)"><Edit /></el-icon>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="source_product_id" label="来源商品 ID" min-width="150" />
        <el-table-column prop="group_name" label="分组" width="110" />
        <el-table-column label="来源" width="100" align="center"><template #default="{ row }"><el-tag size="small">{{ row.platform_name }}</el-tag></template></el-table-column>
        <el-table-column label="已上架" width="80" align="center">
          <template #default="{ row }"><el-tag v-if="row.published" type="success" size="small">{{ row.published }} 店</el-tag><span v-else class="muted">-</span></template>
        </el-table-column>
        <el-table-column label="商品视频" width="90" align="center"><template #default="{ row }">{{ row.has_video ? '有' : '-' }}</template></el-table-column>
        <el-table-column label="价格" min-width="140" align="right"><template #default="{ row }">{{ priceRange(row.price_min, row.price_max) }}</template></el-table-column>
        <el-table-column prop="stock" label="库存" width="90" align="right" />
        <el-table-column prop="sales" label="销量" width="80" align="right" />
        <el-table-column prop="sku_count" label="SKU" width="70" align="center" />
        <el-table-column prop="imported_at" label="导入时间" width="160" />
        <el-table-column label="操作" width="210" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="warning" :loading="row._regen" @click="regenerate([row.id], row)">生成标题</el-button>
            <el-button link type="primary" @click="preview(row)">预览</el-button>
            <el-button link type="primary" @click="preview(row, 'sku')">SKU</el-button>
            <el-button link type="danger" @click="openDelete([row])">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="q.page" v-model:page-size="q.size" :total="total" :page-sizes="[20, 50, 100]"
                       layout="total, sizes, prev, pager, next" @change="load" />
      </div>
    </div>

    <!-- upload -->
    <el-dialog v-model="uploadVisible" title="上传商品数据包" width="520px" @closed="uploadResult = null">
      <el-form label-width="80px">
        <el-form-item label="放入分组">
          <el-select v-model="uploadGroup" placeholder="不分组" clearable style="width: 100%">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="文件">
          <input ref="fileInput" type="file" multiple accept=".json,.txt,.zip" />
        </el-form-item>
      </el-form>
      <div v-if="uploadResult">
        <el-alert :type="uploadResult.failed ? 'warning' : 'success'" :closable="false"
                  :title="`新增 ${uploadResult.created} 个，更新 ${uploadResult.updated} 个，失败 ${uploadResult.failed} 个`" />
        <el-table v-if="uploadResult.errors.length" :data="uploadResult.errors" size="small" max-height="220" style="margin-top: 8px">
          <el-table-column prop="file" label="文件" width="170" show-overflow-tooltip />
          <el-table-column prop="error" label="原因" show-overflow-tooltip />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="uploadVisible = false">关闭</el-button>
        <el-button type="primary" :loading="uploading" @click="doUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- groups -->
    <el-dialog v-model="groupVisible" title="分组管理" width="480px">
      <div style="display: flex; gap: 8px; margin-bottom: 12px">
        <el-input v-model="newGroup" placeholder="新分组名称" />
        <el-button type="primary" @click="createGroup">新增</el-button>
      </div>
      <el-table :data="groups" size="small">
        <el-table-column prop="name" label="分组" />
        <el-table-column prop="count" label="商品数" width="90" />
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="renameGroup(row)">重命名</el-button>
            <el-popconfirm title="删除分组（商品保留）？" @confirm="deleteGroup(row)"><template #reference><el-button link type="danger">删除</el-button></template></el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- delete -->
    <el-dialog v-model="delVisible" :title="`删除 ${delRows.length} 个云库商品`" width="500px">
      <p>适用于货源已下架、断货等不再销售的商品。</p>
      <template v-if="delPublished">
        <el-alert type="warning" :closable="false" show-icon
                  :title="`其中 ${delPublished} 个商品已上架到微信小店，对应的店铺商品如何处理？`" style="margin-bottom: 12px" />
        <el-radio-group v-model="delAction" class="col">
          <el-radio value="delete">同时删除微信小店中的商品（在售的先下架再删除）</el-radio>
          <el-radio value="delisting">只在微信小店中下架，保留商品</el-radio>
          <el-radio value="none">不处理微信小店商品</el-radio>
        </el-radio-group>
      </template>
      <p v-else class="muted">这些商品没有上架到任何店铺，只从云库删除。</p>
      <template #footer><el-button @click="delVisible = false">取消</el-button><el-button type="danger" :loading="deleting" @click="doDelete">确认删除</el-button></template>
    </el-dialog>

    <!-- move -->
    <el-dialog v-model="moveVisible" title="移动分组" width="400px">
      <el-select v-model="moveTarget" placeholder="移出分组（不分组）" clearable style="width: 100%">
        <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
      </el-select>
      <template #footer><el-button @click="moveVisible = false">取消</el-button><el-button type="primary" @click="move">确定</el-button></template>
    </el-dialog>

    <!-- preview -->
    <el-drawer v-model="previewVisible" :title="detail?.title" size="560px">
      <template v-if="detail">
        <el-tabs v-model="previewTab">
          <el-tab-pane label="商品信息" name="info">
            <div class="imgs"><el-image v-for="u in detail.normalized.head_imgs" :key="u" :src="u" fit="cover" :preview-src-list="detail.normalized.head_imgs" /></div>
            <el-descriptions :column="1" border size="small" style="margin-top: 12px">
              <el-descriptions-item label="来源商品 ID">{{ detail.source_product_id }}</el-descriptions-item>
              <el-descriptions-item label="类目 ID">{{ detail.normalized.cats.join(' > ') }}</el-descriptions-item>
              <el-descriptions-item label="视频">{{ detail.normalized.video ? detail.normalized.video.seconds + ' 秒' : '无' }}</el-descriptions-item>
              <el-descriptions-item v-for="a in detail.normalized.attrs" :key="a.name" :label="a.name">{{ a.value }}</el-descriptions-item>
            </el-descriptions>
            <div class="muted" style="margin: 12px 0 6px">详情图 {{ detail.normalized.detail_imgs.length }} 张</div>
            <div class="imgs"><el-image v-for="u in detail.normalized.detail_imgs" :key="u" :src="u" fit="cover" lazy :preview-src-list="detail.normalized.detail_imgs" /></div>
          </el-tab-pane>
          <el-tab-pane label="SKU" name="sku">
            <el-table :data="detail.normalized.skus" size="small" border>
              <el-table-column label="图" width="60"><template #default="{ row }"><el-image :src="row.thumb" style="width: 40px; height: 40px" /></template></el-table-column>
              <el-table-column label="规格"><template #default="{ row }">{{ row.attrs.map((a: any) => `${a.name}:${a.value}`).join('；') || '（未提供规格）' }}</template></el-table-column>
              <el-table-column label="价格" width="90"><template #default="{ row }">¥{{ yuan(row.price) }}</template></el-table-column>
              <el-table-column prop="stock" label="库存" width="90" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http, download, priceRange, yuan, PLATFORMS } from '../api'

const blank = () => ({ name: '', ids: '', platform: '', group_id: undefined as number | undefined,
  price_min: undefined as number | undefined, price_max: undefined as number | undefined, page: 1, size: 20 })
const q = reactive(blank())
const items = ref<any[]>([])
const total = ref(0)
const loading = ref(false)
const selected = ref<any[]>([])
const stats = ref<any[]>([])
const groups = ref<any[]>([])
const platformStats = computed(() => PLATFORMS.map((p) => ({ ...p, count: stats.value.find((s) => s.platform === p.key)?.count ?? 0 })))

const params = () => { const { page, size, ...rest } = q; return Object.fromEntries(Object.entries({ page, size, ...rest }).filter(([, v]) => v !== '' && v !== undefined && v !== null)) }
async function load() {
  loading.value = true
  try { const { data } = await http.get('/library/products', { params: params() }); items.value = data.items; total.value = data.total } finally { loading.value = false }
}
async function loadMeta() {
  const [s, g] = await Promise.all([http.get('/library/stats'), http.get('/library/groups')])
  stats.value = s.data; groups.value = g.data
}
function search() { q.page = 1; load() }
function reset() { Object.assign(q, blank()); load() }
const exportXlsx = () => download('/library/products/export', params(), '商品云库.xlsx')

// upload
const uploadVisible = ref(false)
const uploading = ref(false)
const uploadGroup = ref<number>()
const uploadResult = ref<any>(null)
const fileInput = ref<HTMLInputElement>()
async function doUpload() {
  const files = fileInput.value?.files
  if (!files?.length) return ElMessage.warning('请选择文件')
  const fd = new FormData()
  for (const f of Array.from(files)) fd.append('files', f)
  if (uploadGroup.value) fd.append('group_id', String(uploadGroup.value))
  uploading.value = true
  try { uploadResult.value = (await http.post('/library/upload', fd)).data; load(); loadMeta() } finally { uploading.value = false }
}

// delete (optionally also delete / delist the copies already published to WeChat stores)
const delVisible = ref(false)
const delRows = ref<any[]>([])
const delAction = ref('delete')
const deleting = ref(false)
const delPublished = computed(() => delRows.value.filter((r) => r.published).length)
function openDelete(rows: any[]) { delRows.value = rows; delAction.value = 'delete'; delVisible.value = true }
async function doDelete() {
  deleting.value = true
  try {
    const { data } = await http.post('/library/products/delete', {
      ids: delRows.value.map((r) => r.id), store_action: delPublished.value ? delAction.value : 'none' })
    delVisible.value = false
    const bad = data.store_results.filter((r: any) => !r.ok)
    if (bad.length) {
      ElMessageBox.alert(bad.map((b: any) => `${b.store} / ${b.title}：${b.error}`).join('<br>'),
        `已删除 ${data.deleted} 个云库商品；微信小店处理失败 ${bad.length} 个`, { dangerouslyUseHTMLString: true })
    } else {
      const done = data.store_results.length
      ElMessage.success(`已删除 ${data.deleted} 个云库商品` + (done ? `，并${delAction.value === 'delete' ? '删除' : '下架'}微信小店商品 ${done} 个` : ''))
    }
    load(); loadMeta()
  } finally { deleting.value = false }
}
const moveVisible = ref(false)
const moveTarget = ref<number>()
async function move() {
  await http.post('/library/products/move', { ids: selected.value.map((r) => r.id), group_id: moveTarget.value ?? null })
  moveVisible.value = false; load(); loadMeta()
}

// groups
const groupVisible = ref(false)
const newGroup = ref('')
async function createGroup() { if (!newGroup.value) return; await http.post('/library/groups', { name: newGroup.value }); newGroup.value = ''; loadMeta() }
async function renameGroup(g: any) {
  const { value } = await ElMessageBox.prompt('新名称', '重命名分组', { inputValue: g.name })
  await http.put(`/library/groups/${g.id}`, { name: value }); loadMeta(); load()
}
async function deleteGroup(g: any) { await http.delete(`/library/groups/${g.id}`); loadMeta(); load() }

// titles
const regenRunning = ref(false)
const regenDone = ref(0)
const regenTotal = ref(0)
async function regenerate(ids: number[], row?: any) {
  if (row) row._regen = true
  else { regenRunning.value = true; regenDone.value = 0; regenTotal.value = ids.length }
  let failed = 0
  try {
    for (let i = 0; i < ids.length; i += 10) {
      const { data } = await http.post('/library/products/regenerate-titles', { ids: ids.slice(i, i + 10) })
      for (const r of data.results) {
        const it = items.value.find((x) => x.id === r.id)
        if (r.ai_title && it) it.ai_title = r.ai_title
        if (r.error) failed++
      }
      regenDone.value = Math.min(i + 10, ids.length)
    }
    failed ? ElMessage.warning(`完成，${failed} 个生成失败，可重试`) : ElMessage.success(row ? '已生成新标题' : `已为 ${ids.length} 个商品生成新标题`)
  } finally {
    if (row) row._regen = false
    regenRunning.value = false
  }
}
async function editTitle(row: any) {
  const { value } = await ElMessageBox.prompt(`原标题：${row.title}`, '修改新标题（留空则发布时自动生成）', {
    inputValue: row.ai_title || '', inputValidator: (v: string) => (v || '').length <= 60 || '最多 60 个字',
  })
  const { data } = await http.put(`/library/products/${row.id}/title`, { ai_title: value || '' })
  row.ai_title = data.ai_title
  ElMessage.success('已保存')
}

// preview
const previewVisible = ref(false)
const previewTab = ref('info')
const detail = ref<any>(null)
async function preview(row: any, tab = 'info') {
  detail.value = (await http.get(`/library/products/${row.id}`)).data
  previewTab.value = tab; previewVisible.value = true
}

onMounted(() => { load(); loadMeta() })
</script>

<style scoped>
.platforms { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.pcard { display: flex; gap: 12px; align-items: center; border: 1px solid #eef0f4; border-radius: 8px; padding: 14px; cursor: pointer; background: #fafbfc; }
.pcard.active { border-color: #409eff; background: #ecf5ff; }
.pname { font-weight: 600; margin-bottom: 4px; }
.newtitle { display: flex; align-items: center; gap: 6px; }
.newtitle .ellipsis { flex: 1; min-width: 0; }
.col { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.edit { cursor: pointer; color: #409eff; flex-shrink: 0; }
.imgs { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.imgs .el-image { width: 100%; aspect-ratio: 1; border-radius: 4px; }
</style>

<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">发布配置</div>
        <div>
          <el-button type="primary" icon="Plus" @click="openEditor(null)">新增上货模板</el-button>
          <el-button icon="Tickets" @click="listVisible = true">查看上货模板列表</el-button>
          <el-button type="success" icon="Goods" @click="openSetProducts">设置上货商品</el-button>
        </div>
      </div>
      <el-form :inline="true">
        <el-form-item label="店铺名称"><el-input v-model="filter" placeholder="请输入店铺名称" clearable /></el-form-item>
        <el-form-item><el-button type="primary" icon="Search" @click="load">查询</el-button><el-button icon="Refresh" @click="filter = ''; load()">重置</el-button></el-form-item>
      </el-form>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">店铺列表</div>
        <div>
          <span class="muted">请选择后续需要配置发布规则的店铺</span>
          <el-button class="ml" size="small" :disabled="!selected.length" @click="openConfig(selected)">批量配置（{{ selected.length }}）</el-button>
        </div>
      </div>
      <el-table ref="tableRef" :data="stores" v-loading="loading" border row-key="id" @selection-change="(v: any[]) => (selected = v)">
        <el-table-column type="selection" width="45" align="center" />
        <el-table-column type="index" label="序号" width="60" align="center" />
        <el-table-column label="店铺" min-width="160">
          <template #default="{ row }"><div class="shop"><el-avatar :src="row.logo" :size="30">{{ row.name.slice(0, 1) }}</el-avatar>{{ row.name }}</div></template>
        </el-table-column>
        <el-table-column prop="appid" label="AppID" min-width="160" />
        <el-table-column prop="wx_shop_id" label="微信店铺 ID" min-width="140" />
        <el-table-column label="开店状态" width="130" align="center"><template #default="{ row }"><el-tag type="info">{{ row.open_status || '-' }}</el-tag></template></el-table-column>
        <el-table-column label="运费模板" min-width="170" align="center">
          <template #default="{ row }"><el-link type="primary" @click="openConfig([row])">{{ row.freight_template_name || '去设置' }}</el-link></template>
        </el-table-column>
        <el-table-column label="退货地址模板" min-width="180" align="center">
          <template #default="{ row }"><el-link type="primary" @click="openConfig([row])"><span class="ellipsis addr">{{ row.address_name || '去设置' }}</span></el-link></template>
        </el-table-column>
        <el-table-column label="上货模板" min-width="140" align="center">
          <template #default="{ row }"><el-link type="primary" @click="openConfig([row])">{{ row.publish_template_name || '去设置' }}</el-link></template>
        </el-table-column>
        <el-table-column label="配置状态" width="100" align="center">
          <template #default="{ row }"><el-tag :type="row.config_status === 'configured' ? 'success' : 'info'">{{ row.config_status === 'configured' ? '已配置' : '待配置' }}</el-tag></template>
        </el-table-column>
      </el-table>
    </div>

    <TemplateEditor v-model="editorVisible" :id="editingId" @saved="loadTemplates(); load()" />

    <!-- template list -->
    <el-dialog v-model="listVisible" title="上货模板列表" width="760px">
      <el-table :data="templates" border>
        <el-table-column type="index" label="序号" width="60" align="center" />
        <el-table-column prop="name" label="模板名称" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEditor(row.id)">编辑</el-button>
            <el-popconfirm title="删除后使用该模板的店铺需重新配置，确定？" @confirm="removeTemplate(row)"><template #reference><el-button link type="danger">删除</el-button></template></el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <template #footer><el-button @click="listVisible = false">关闭</el-button></template>
    </el-dialog>

    <!-- store config -->
    <el-dialog v-model="configVisible" :title="configStores.length > 1 ? `批量配置 ${configStores.length} 个店铺` : `配置：${configStores[0]?.name}`" width="560px">
      <el-form label-width="100px" v-loading="optionsLoading">
        <el-alert v-for="e in optionErrors" :key="e" type="error" :title="e" :closable="false" style="margin-bottom: 8px" />
        <el-alert v-if="configStores.length > 1" type="info" :closable="false" style="margin-bottom: 8px"
                  title="运费模板和退货地址读取自第一个店铺；各店铺的模板 ID 不同时，请逐个配置。" />
        <el-form-item label="运费模板">
          <el-select v-model="cfg.freight_template_id" placeholder="选择运费模板" clearable style="width: 100%">
            <el-option v-for="f in options.freight_templates" :key="f.id" :label="f.name" :value="f.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="退货地址">
          <el-select v-model="cfg.address_id" placeholder="选择退货地址" clearable style="width: 100%">
            <el-option v-for="a in options.addresses" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="上货模板">
          <el-select v-model="cfg.publish_template_id" placeholder="选择上货模板" clearable style="width: 100%">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="configVisible = false">取消</el-button><el-button type="primary" @click="saveConfig">保存</el-button></template>
    </el-dialog>

    <!-- set products -->
    <el-dialog v-model="setVisible" title="设置上货商品" width="840px">
      <div class="choose">
        <div class="choose-title"><el-tag effect="dark" round>2</el-tag> 方式二：云端仓库数据</div>
        <div class="plats">
          <div v-for="p in PLATFORMS" :key="p.key" class="plat" :class="{ active: platform === p.key }" @click="platform = p.key; loadGroups()">
            <span class="platform-icon" :style="{ background: p.color, width: '24px', height: '24px', fontSize: p.short.length > 1 ? '8px' : '13px' }">{{ p.short }}</span>{{ p.name }}
          </div>
        </div>
        <div class="card-head" style="margin: 14px 0 8px">
          <span>对应平台：{{ PLATFORMS.find((p) => p.key === platform)?.name }}</span>
          <el-input v-model="groupFilter" placeholder="搜索商品分组" style="width: 220px" clearable />
        </div>
        <div class="groups">
          <div class="group" :class="{ active: groupId === null }" @click="groupId = null">
            <div class="b">全部商品</div><div class="muted">{{ platformTotal }} 条数据</div>
            <el-icon v-if="groupId === null" class="check"><Check /></el-icon>
          </div>
          <div v-for="g in shownGroups" :key="g.id" class="group" :class="{ active: groupId === g.id }" @click="groupId = g.id">
            <div class="b">{{ g.name }}</div><div class="muted">{{ g.count }} 条数据</div>
            <el-icon v-if="groupId === g.id" class="check"><Check /></el-icon>
          </div>
        </div>
      </div>
      <p class="muted">将为已选的 {{ selected.length }} 个店铺各创建一个发布任务，由后台 Worker 逐个商品执行。</p>
      <template #footer><el-button @click="setVisible = false">取消</el-button><el-button type="primary" :loading="creating" @click="createTasks">确定</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { http, PLATFORMS } from '../api'
import TemplateEditor from '../components/TemplateEditor.vue'

const router = useRouter()
const stores = ref<any[]>([])
const selected = ref<any[]>([])
const loading = ref(false)
const filter = ref('')
const templates = ref<any[]>([])

const tableRef = ref<any>()
async function load() {
  const keep = new Set(selected.value.map((s) => s.id))
  loading.value = true
  try { stores.value = (await http.get('/stores', { params: { name: filter.value } })).data } finally { loading.value = false }
  await nextTick()  // keep the user's store selection across reloads
  stores.value.filter((s) => keep.has(s.id)).forEach((s) => tableRef.value?.toggleRowSelection(s, true))
}
const loadTemplates = async () => { templates.value = (await http.get('/templates')).data }

// template editor / list
const editorVisible = ref(false)
const editingId = ref<number | null>(null)
const listVisible = ref(false)
function openEditor(id: number | null) { editingId.value = id; editorVisible.value = true }
async function removeTemplate(t: any) { await http.delete(`/templates/${t.id}`); loadTemplates(); load() }

// store config
const configVisible = ref(false)
const configStores = ref<any[]>([])
const optionsLoading = ref(false)
const options = ref<any>({ freight_templates: [], addresses: [] })
const optionErrors = ref<string[]>([])
const cfg = reactive<any>({ freight_template_id: '', address_id: '', publish_template_id: null })
async function openConfig(rows: any[]) {
  configStores.value = rows
  const first = rows[0]
  Object.assign(cfg, { freight_template_id: first.freight_template_id || '', address_id: first.address_id || '', publish_template_id: first.publish_template_id })
  configVisible.value = true
  optionsLoading.value = true
  try {
    const { data } = await http.get(`/stores/${first.id}/options`)
    options.value = data; optionErrors.value = data.errors
  } finally { optionsLoading.value = false }
}
async function saveConfig() {
  const f = options.value.freight_templates.find((x: any) => x.id === cfg.freight_template_id)
  const a = options.value.addresses.find((x: any) => x.id === cfg.address_id)
  const body: any = { publish_template_id: cfg.publish_template_id ?? undefined }
  if (f) Object.assign(body, { freight_template_id: f.id, freight_template_name: f.name })
  if (a) Object.assign(body, { address_id: a.id, address_name: a.name })
  await http.post('/stores/bulk-config', { ...body, store_ids: configStores.value.map((s) => s.id) })
  ElMessage.success('已保存'); configVisible.value = false; load()
}

// set products -> tasks
const setVisible = ref(false)
const platform = ref('wechat')
const groups = ref<any[]>([])
const groupId = ref<number | null>(null)
const groupFilter = ref('')
const platformTotal = ref(0)
const creating = ref(false)
const shownGroups = computed(() => groups.value.filter((g) => g.count > 0 && g.name.includes(groupFilter.value)))
async function loadGroups() {
  const [g, s] = await Promise.all([http.get('/library/groups', { params: { platform: platform.value } }), http.get('/library/stats')])
  groups.value = g.data
  platformTotal.value = s.data.find((x: any) => x.platform === platform.value)?.count ?? 0
}
function openSetProducts() {
  if (!selected.value.length) return ElMessage.warning('请先在店铺列表中勾选店铺')
  const bad = selected.value.filter((s) => s.config_status !== 'configured')
  if (bad.length) return ElMessage.warning(`以下店铺未完成配置：${bad.map((s) => s.name).join('、')}`)
  groupId.value = null; setVisible.value = true; loadGroups()
}
async function createTasks() {
  creating.value = true
  try {
    const { data } = await http.post('/tasks', { store_ids: selected.value.map((s) => s.id), platform: platform.value, group_id: groupId.value })
    ElMessage.success(`已创建 ${data.length} 个发布任务`)
    setVisible.value = false; router.push('/tasks')
  } finally { creating.value = false }
}

onMounted(() => { load(); loadTemplates() })
</script>

<style scoped>
.shop { display: flex; align-items: center; gap: 8px; }
.ml { margin-left: 10px; }
.addr { display: inline-block; max-width: 170px; }
.choose { background: #ecf5ff; border: 1px solid #b3d8ff; border-radius: 8px; padding: 14px; }
.choose-title { font-weight: 600; margin-bottom: 12px; }
.plats { display: flex; gap: 10px; flex-wrap: wrap; }
.plat { display: flex; gap: 8px; align-items: center; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px 16px; cursor: pointer; }
.plat.active { border-color: #409eff; color: #409eff; }
.groups { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.group { position: relative; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; cursor: pointer; }
.group.active { border-color: #409eff; box-shadow: 0 0 0 1px #409eff; }
.group .b { font-weight: 600; margin-bottom: 6px; }
.check { position: absolute; right: 12px; top: 12px; color: #409eff; }
</style>

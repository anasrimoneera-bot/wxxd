<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">用户管理</div>
        <el-button type="primary" icon="Plus" @click="openCreate">新增客户</el-button>
      </div>
      <el-form :inline="true">
        <el-form-item label="搜索"><el-input v-model="q" placeholder="公司 / 账号 / 备注" clearable style="width: 220px" @keyup.enter="load" /></el-form-item>
        <el-form-item label="状态">
          <el-select v-model="status" placeholder="全部" clearable style="width: 140px" @change="load">
            <el-option label="正常" value="active" /><el-option label="已到期" value="expired" /><el-option label="已停用" value="disabled" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" icon="Search" @click="load">查询</el-button></el-form-item>
      </el-form>
      <p class="muted" style="margin: 0">客户到期或停用后无法登录，未执行的发布任务也会停止。续费后立即恢复，数据保留。</p>
    </div>

    <div class="card">
      <el-table :data="rows" v-loading="loading" border>
        <el-table-column label="公司 / 账号" min-width="180">
          <template #default="{ row }">
            <div class="b">{{ row.company }} <el-tag v-if="row.is_admin" size="small" type="warning">管理员</el-tag></div>
            <div class="muted">{{ row.username }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }"><el-tag :type="statusType[row.status]">{{ statusName[row.status] }}</el-tag></template>
        </el-table-column>
        <el-table-column label="会员到期" width="150">
          <template #default="{ row }">
            <template v-if="row.expires_at">
              <div>{{ row.expires_at }}</div>
              <div :class="row.days_left < 0 ? 'danger' : row.days_left <= 7 ? 'warn' : 'muted'">
                {{ row.days_left < 0 ? `已过期 ${-row.days_left} 天` : `剩余 ${row.days_left} 天` }}
              </div>
            </template>
            <span v-else class="muted">不限期</span>
          </template>
        </el-table-column>
        <el-table-column label="积分" width="90" align="center">
          <template #default="{ row }"><el-link type="warning" :underline="false" @click="openLogs(row)">{{ row.points }}</el-link></template>
        </el-table-column>
        <el-table-column label="打单" width="150" align="center">
          <template #default="{ row }">
            <el-switch :model-value="row.print_enabled" :disabled="row.is_admin" size="small" @change="(v: any) => setPrint(row, !!v)" />
            <div v-if="row.print_enabled || row.print_count" class="small">
              <el-link type="primary" :underline="false" @click="openPrintLogs(row)">已打 {{ row.print_count }} 单</el-link>
              <span v-if="row.print_reprints" class="muted">（补打 {{ row.print_reprints }}）</span>
              <el-popconfirm title="把这个账号的打单数量清零？（打单记录会保留）" @confirm="resetPrint(row)">
                <template #reference><el-link type="danger" :underline="false" style="margin-left: 6px">清零</el-link></template>
              </el-popconfirm>
            </div>
            <div v-if="row.print_reset_at" class="muted small">{{ row.print_reset_at }} 起</div>
          </template>
        </el-table-column>
        <el-table-column label="每日上传" width="90" align="center">
          <template #default="{ row }">{{ row.daily_upload_limit == null ? '默认' : row.daily_upload_limit === 0 ? '不限' : row.daily_upload_limit }}</template>
        </el-table-column>
        <el-table-column label="店铺" width="80" align="center">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="openStores(row)">{{ row.stores }}</el-link>
            <div v-if="row.shared_stores" class="muted small">分配 {{ row.shared_stores }}</div>
          </template>
        </el-table-column>
        <el-table-column label="云库商品" prop="products" width="90" align="center" />
        <el-table-column label="任务" prop="tasks" width="70" align="center" />
        <el-table-column label="子账号" prop="subaccounts" width="80" align="center" />
        <el-table-column label="备注" prop="note" min-width="140" show-overflow-tooltip />
        <el-table-column label="创建时间" prop="created_at" width="150" />
        <el-table-column label="操作" width="360" align="center" fixed="right">
          <template #default="{ row }">
            <el-dropdown @command="(m: number) => extend(row, m)">
              <el-button link type="primary">续费 <el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown><el-dropdown-menu>
                <el-dropdown-item v-for="m in [1, 3, 6, 12]" :key="m" :command="m">续费 {{ m }} 个月</el-dropdown-item>
              </el-dropdown-menu></template>
            </el-dropdown>
            <el-button link type="warning" @click="openTopup(row)">积分</el-button>
            <el-button link type="primary" @click="openStores(row)">分配店铺</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="resetPwd(row)">重置密码</el-button>
            <el-button v-if="!row.is_admin" link :type="row.active ? 'danger' : 'success'" @click="toggle(row)">{{ row.active ? '停用' : '启用' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="新增客户" width="460px">
      <el-form :model="cf" label-width="90px">
        <el-form-item label="登录账号" required><el-input v-model="cf.username" autocomplete="off" /></el-form-item>
        <el-form-item label="初始密码" required><el-input v-model="cf.password" autocomplete="new-password" show-password /></el-form-item>
        <el-form-item label="公司名称"><el-input v-model="cf.company" /></el-form-item>
        <el-form-item label="会员时长">
          <el-select v-model="cf.months" style="width: 100%">
            <el-option v-for="m in [1, 3, 6, 12]" :key="m" :label="`${m} 个月`" :value="m" /><el-option label="不限期" :value="0" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="cf.note" placeholder="如：联系方式、收费方式" /></el-form-item>
      </el-form>
      <p class="muted">把账号和初始密码发给客户，客户登录后可自行添加店铺和子账号。</p>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="create">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑客户" width="460px">
      <el-form :model="ef" label-width="90px">
        <el-form-item label="公司名称"><el-input v-model="ef.company" /></el-form-item>
        <el-form-item label="会员到期">
          <el-date-picker v-model="ef.expires_at" type="date" value-format="YYYY-MM-DD" placeholder="不限期" clearable style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="ef.note" /></el-form-item>
        <el-form-item label="每日上传">
          <el-radio-group v-model="ef.limitMode">
            <el-radio value="default">按系统设置</el-radio><el-radio value="custom">单独设置</el-radio>
          </el-radio-group>
          <el-input-number v-if="ef.limitMode === 'custom'" v-model="ef.daily_upload_limit" :min="0" :max="1000000" style="margin-top: 6px" />
          <span v-if="ef.limitMode === 'custom'" class="muted" style="margin-left: 8px">个 / 天（0 = 不限）</span>
        </el-form-item>
      </el-form>
      <p class="muted">清空到期日期表示不限期。</p>
      <template #footer><el-button @click="editVisible = false">取消</el-button><el-button type="primary" @click="saveEdit">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="storesVisible" :title="`店铺 · ${storesFor.company}`" width="720px">
      <div v-loading="storesLoading">
        <div class="sec">自己的店铺</div>
        <div v-if="!storeData.own.length" class="muted small">没有</div>
        <el-tag v-for="s in storeData.own" :key="s.id" class="stag">{{ s.name }}</el-tag>

        <div class="sec">分配给该用户使用的店铺 <span class="muted small">（该用户可管理商品、订单、发货、上货，与店铺所属账号看到的是同一份数据；店铺设置仍由所属账号修改）</span></div>
        <el-table :data="storeData.assigned" size="small" border empty-text="还没有分配">
          <el-table-column prop="name" label="店铺" />
          <el-table-column prop="owner_name" label="所属账号" width="160" />
          <el-table-column prop="granted_at" label="分配时间" width="150" />
          <el-table-column label="操作" width="90" align="center">
            <template #default="{ row }">
              <el-popconfirm title="取消分配后该用户将看不到这个店铺（店铺和数据不受影响），确定？" @confirm="unassign(row)">
                <template #reference><el-button link type="danger">取消分配</el-button></template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>

        <div class="sec">分配店铺</div>
        <div style="display: flex; gap: 8px">
          <el-select v-model="toAssign" multiple filterable placeholder="选择要分配给该用户的店铺" style="flex: 1">
            <el-option v-for="s in storeData.available" :key="s.id" :label="`${s.name}（${s.owner_name}）`" :value="s.id" />
          </el-select>
          <el-button type="primary" :disabled="!toAssign.length" :loading="assigning" @click="assign">分配</el-button>
        </div>
      </div>
    </el-dialog>

    <el-dialog v-model="topupVisible" :title="`积分 · ${tf.company}`" width="440px">
      <p>当前积分：<b>{{ tf.points }}</b></p>
      <el-form label-width="80px">
        <el-form-item label="操作">
          <el-radio-group v-model="tf.mode"><el-radio value="add">充值（增加）</el-radio><el-radio value="sub">扣减</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="积分"><el-input-number v-model="tf.amount" :min="1" :max="100000000" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="tf.note" placeholder="如：微信转账 100 元" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="topupVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="topup">确定</el-button></template>
    </el-dialog>

    <el-dialog v-model="logsVisible" :title="`积分记录 · ${logsFor.company}`" width="680px">
      <el-table :data="logs" size="small" max-height="420">
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column label="变动" width="80" align="right">
          <template #default="{ row }"><span :class="row.change > 0 ? 'plus' : 'danger'">{{ row.change > 0 ? '+' : '' }}{{ row.change }}</span></template>
        </el-table-column>
        <el-table-column prop="balance" label="余额" width="80" align="right" />
        <el-table-column prop="note" label="说明" show-overflow-tooltip />
        <el-table-column prop="operator" label="操作人" width="100" />
      </el-table>
      <div style="margin-top: 10px; text-align: right">
        <el-pagination v-model:current-page="logsPage" :total="logsTotal" :page-size="20" layout="total, prev, pager, next" @current-change="loadLogs" />
      </div>
    </el-dialog>

    <el-dialog v-model="plVisible" :title="`打单记录 · ${plFor?.company || ''}`" width="760px">
      <el-table :data="plRows" size="small" max-height="460" v-loading="plLoading">
        <el-table-column prop="time" label="时间" width="160" />
        <el-table-column prop="username" label="账号" width="110" />
        <el-table-column prop="order_id" label="订单号" min-width="170" />
        <el-table-column prop="waybill_id" label="运单号" min-width="140" />
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }"><el-tag size="small" :type="row.re_print ? 'info' : 'success'">{{ row.re_print ? '补打' : '打单' }}</el-tag><div v-if="!row.counted" class="muted small">清零前</div></template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http } from '../api'

const statusName: Record<string, string> = { active: '正常', expired: '已到期', disabled: '已停用' }
const statusType: Record<string, any> = { active: 'success', expired: 'warning', disabled: 'info' }
const rows = ref<any[]>([])
const loading = ref(false)
const q = ref('')
const status = ref('')
const saving = ref(false)

async function load() {
  loading.value = true
  try { rows.value = (await http.get('/admin/tenants', { params: { q: q.value, status: status.value } })).data } finally { loading.value = false }
}

const createVisible = ref(false)
const cf = reactive({ username: '', password: '', company: '', months: 1, note: '' })
function openCreate() { Object.assign(cf, { username: '', password: '', company: '', months: 1, note: '' }); createVisible.value = true }
async function create() {
  saving.value = true
  try { await http.post('/admin/tenants', cf); ElMessage.success('已创建'); createVisible.value = false; load() } finally { saving.value = false }
}

const editVisible = ref(false)
const ef = reactive<any>({ id: 0, company: '', expires_at: null, note: '', limitMode: 'default', daily_upload_limit: 0 })
function openEdit(r: any) {
  Object.assign(ef, { id: r.id, company: r.company, expires_at: r.expires_at, note: r.note,
    limitMode: r.daily_upload_limit == null ? 'default' : 'custom', daily_upload_limit: r.daily_upload_limit ?? 500 })
  editVisible.value = true
}
async function saveEdit() {
  await http.put(`/admin/tenants/${ef.id}`, { company: ef.company, note: ef.note, expires_at: ef.expires_at || '',
    ...(ef.limitMode === 'default' ? { use_default_upload_limit: true } : { daily_upload_limit: ef.daily_upload_limit }) })
  ElMessage.success('已保存'); editVisible.value = false; load()
}

async function extend(r: any, months: number) {
  const { data } = await http.post(`/admin/tenants/${r.id}/extend`, { months })
  ElMessage.success(`已续费 ${months} 个月，到期日 ${data.expires_at}`); load()
}
async function toggle(r: any) {
  if (r.active) await ElMessageBox.confirm(`停用「${r.company}」？停用后该客户无法登录，数据保留。`, '确认停用', { type: 'warning' })
  await http.put(`/admin/tenants/${r.id}`, { active: !r.active }); load()
}
async function resetPwd(r: any) {
  const { value } = await ElMessageBox.prompt(`为 ${r.username} 设置新密码（至少 6 位）`, '重置密码', {
    inputType: 'password', inputPattern: /^.{6,64}$/, inputErrorMessage: '至少 6 位',
  })
  await http.post(`/admin/tenants/${r.id}/reset-password`, { password: value }); ElMessage.success('密码已重置')
}
// 分配店铺
const storesVisible = ref(false)
const storesLoading = ref(false)
const storesFor = ref<any>({})
const storeData = ref<any>({ own: [], assigned: [], available: [] })
const toAssign = ref<number[]>([])
const assigning = ref(false)
async function openStores(r: any) {
  storesFor.value = r; toAssign.value = []; storesVisible.value = true; storesLoading.value = true
  try { storeData.value = (await http.get(`/admin/tenants/${r.id}/stores`)).data } finally { storesLoading.value = false }
}
async function assign() {
  assigning.value = true
  try {
    const { data } = await http.post(`/admin/tenants/${storesFor.value.id}/stores`, { store_ids: toAssign.value })
    storeData.value = data; toAssign.value = []
    ElMessage.success(`已分配 ${data.added} 个店铺`); load()
  } finally { assigning.value = false }
}
async function unassign(s: any) {
  storeData.value = (await http.delete(`/admin/tenants/${storesFor.value.id}/stores/${s.id}`)).data
  ElMessage.success('已取消分配'); load()
}

// 积分
const topupVisible = ref(false)
const tf = reactive<any>({ id: 0, company: '', points: 0, mode: 'add', amount: 100, note: '' })
function openTopup(r: any) { Object.assign(tf, { id: r.id, company: r.company, points: r.points, mode: 'add', amount: 100, note: '' }); topupVisible.value = true }
async function topup() {
  saving.value = true
  try {
    const change = tf.mode === 'add' ? tf.amount : -tf.amount
    const { data } = await http.post(`/admin/tenants/${tf.id}/points`, { change, note: tf.note })
    ElMessage.success(`已${tf.mode === 'add' ? '充值' : '扣减'}，当前积分 ${data.points}`)
    topupVisible.value = false; load()
  } finally { saving.value = false }
}
const logsVisible = ref(false)
const logsFor = ref<any>({})
const logs = ref<any[]>([])
const logsTotal = ref(0)
const logsPage = ref(1)
async function loadLogs() {
  const { data } = await http.get(`/admin/tenants/${logsFor.value.id}/points/logs`, { params: { page: logsPage.value, size: 20 } })
  logs.value = data.items; logsTotal.value = data.total
}
function openLogs(r: any) { logsFor.value = r; logsPage.value = 1; logsVisible.value = true; loadLogs() }

// 打单权限 / 数量
async function setPrint(row: any, enabled: boolean) {
  const { data } = await http.put(`/admin/tenants/${row.id}/print`, { enabled })
  Object.assign(row, data); ElMessage.success(enabled ? '已开通打单' : '已关闭打单')
}
async function resetPrint(row: any) {
  const { data } = await http.post(`/admin/tenants/${row.id}/print/reset`)
  Object.assign(row, data); ElMessage.success('已清零')
}
const plVisible = ref(false)
const plLoading = ref(false)
const plFor = ref<any>(null)
const plRows = ref<any[]>([])
async function openPrintLogs(row: any) {
  plFor.value = row; plVisible.value = true; plLoading.value = true
  try { plRows.value = (await http.get(`/admin/tenants/${row.id}/print/logs`)).data } finally { plLoading.value = false }
}
onMounted(load)
</script>

<style scoped>
.b { font-weight: 600; }
.warn { color: #e6a23c; }
.danger { color: #f56c6c; }
.plus { color: #67c23a; }
.small { font-size: 12px; }
.sec { font-weight: 600; margin: 14px 0 8px; }
.sec:first-child { margin-top: 0; }
.stag { margin: 0 6px 6px 0; }
</style>

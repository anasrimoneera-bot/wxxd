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
        <el-table-column label="店铺" prop="stores" width="70" align="center" />
        <el-table-column label="云库商品" prop="products" width="90" align="center" />
        <el-table-column label="任务" prop="tasks" width="70" align="center" />
        <el-table-column label="子账号" prop="subaccounts" width="80" align="center" />
        <el-table-column label="备注" prop="note" min-width="140" show-overflow-tooltip />
        <el-table-column label="创建时间" prop="created_at" width="150" />
        <el-table-column label="操作" width="250" align="center" fixed="right">
          <template #default="{ row }">
            <el-dropdown @command="(m: number) => extend(row, m)">
              <el-button link type="primary">续费 <el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown><el-dropdown-menu>
                <el-dropdown-item v-for="m in [1, 3, 6, 12]" :key="m" :command="m">续费 {{ m }} 个月</el-dropdown-item>
              </el-dropdown-menu></template>
            </el-dropdown>
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
      </el-form>
      <p class="muted">清空到期日期表示不限期。</p>
      <template #footer><el-button @click="editVisible = false">取消</el-button><el-button type="primary" @click="saveEdit">保存</el-button></template>
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
const ef = reactive<any>({ id: 0, company: '', expires_at: null, note: '' })
function openEdit(r: any) { Object.assign(ef, { id: r.id, company: r.company, expires_at: r.expires_at, note: r.note }); editVisible.value = true }
async function saveEdit() {
  await http.put(`/admin/tenants/${ef.id}`, { company: ef.company, note: ef.note, expires_at: ef.expires_at || '' })
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
onMounted(load)
</script>

<style scoped>
.b { font-weight: 600; }
.warn { color: #e6a23c; }
.danger { color: #f56c6c; }
</style>

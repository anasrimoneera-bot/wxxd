<template>
  <div class="card">
    <div class="card-head">
      <div class="card-title">子账号管理</div>
      <el-button type="primary" icon="Plus" @click="openForm(null)">新增子账号</el-button>
    </div>
    <p class="muted">子账号可使用店铺、云库、发布和任务功能，不能管理子账号。</p>
    <el-table :data="subs" border>
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="display_name" label="名称" />
      <el-table-column label="状态" width="120"><template #default="{ row }"><el-switch :model-value="row.active" @change="toggle(row)" /></template></el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="160" align="center">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">修改</el-button>
          <el-popconfirm title="删除该子账号？" @confirm="remove(row)"><template #reference><el-button link type="danger">删除</el-button></template></el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
    <el-dialog v-model="formVisible" :title="editing ? '修改子账号' : '新增子账号'" width="420px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户名"><el-input v-model="form.username" :disabled="!!editing" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item :label="editing ? '新密码' : '密码'"><el-input v-model="form.password" type="password" show-password :placeholder="editing ? '不修改请留空' : ''" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="formVisible = false">取消</el-button><el-button type="primary" @click="save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const subs = ref<any[]>([])
const formVisible = ref(false)
const editing = ref<any>(null)
const form = reactive({ username: '', display_name: '', password: '' })
const load = async () => { subs.value = (await http.get('/subaccounts')).data }
function openForm(row: any) {
  editing.value = row
  Object.assign(form, { username: row?.username ?? '', display_name: row?.display_name ?? '', password: '' })
  formVisible.value = true
}
async function save() {
  if (editing.value) await http.put(`/subaccounts/${editing.value.id}`, { display_name: form.display_name, password: form.password || undefined })
  else await http.post('/subaccounts', form)
  ElMessage.success('已保存'); formVisible.value = false; load()
}
async function toggle(row: any) { await http.put(`/subaccounts/${row.id}`, { active: !row.active }); load() }
async function remove(row: any) { await http.delete(`/subaccounts/${row.id}`); load() }
onMounted(load)
</script>

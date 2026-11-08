<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">店铺管理</div>
        <el-button type="primary" icon="Plus" @click="addVisible = true">添加店铺</el-button>
      </div>
      <el-alert type="info" :closable="false" show-icon>
        添加前请在微信小店后台「服务市场 / 开发 → 基本配置」获取 AppID 与 AppSecret，并把服务器公网 IP
        <b>{{ meta.server_public_ip || '（部署后在此显示）' }}</b> 加入 API IP 白名单。
      </el-alert>
    </div>
    <div class="card">
      <el-table :data="stores" v-loading="loading" border>
        <el-table-column type="index" label="序号" width="60" align="center" />
        <el-table-column label="店铺" min-width="180">
          <template #default="{ row }">
            <div class="shop"><el-avatar :src="row.logo" :size="32">{{ row.name.slice(0, 1) }}</el-avatar><span>{{ row.name }}</span></div>
          </template>
        </el-table-column>
        <el-table-column prop="appid" label="AppID" min-width="170" />
        <el-table-column prop="wx_shop_id" label="微信店铺 ID" min-width="150" />
        <el-table-column label="开店状态" width="130" align="center">
          <template #default="{ row }"><el-tag type="info">{{ row.open_status || '-' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="连接" min-width="160">
          <template #default="{ row }">
            <el-text v-if="row.last_error" type="danger" size="small">{{ row.last_error }}</el-text>
            <el-text v-else type="success" size="small">正常</el-text>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="refresh(row)">刷新</el-button>
            <el-button link type="primary" @click="$router.push('/publish')">发布配置</el-button>
            <el-popconfirm title="删除后该店铺的配置将清除，确定？" @confirm="remove(row)">
              <template #reference><el-button link type="danger">删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="addVisible" title="添加店铺" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="AppID" required><el-input v-model="form.appid" placeholder="wx 开头" /></el-form-item>
        <el-form-item label="AppSecret" required><el-input v-model="form.secret" type="password" show-password /></el-form-item>
        <el-form-item label="店铺备注"><el-input v-model="form.name" placeholder="留空自动读取小店名称" /></el-form-item>
      </el-form>
      <p class="muted">AppSecret 加密保存，不会再显示。保存时会立即测试连接。</p>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="add">测试并保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const stores = ref<any[]>([])
const loading = ref(false)
const addVisible = ref(false)
const saving = ref(false)
const form = reactive({ appid: '', secret: '', name: '' })
const meta = ref<any>({})

async function load() {
  loading.value = true
  try { stores.value = (await http.get('/stores')).data } finally { loading.value = false }
}
async function add() {
  saving.value = true
  try {
    await http.post('/stores', form)
    ElMessage.success('店铺已连接')
    addVisible.value = false
    Object.assign(form, { appid: '', secret: '', name: '' })
    load()
  } finally { saving.value = false }
}
async function refresh(row: any) {
  const { data } = await http.post(`/stores/${row.id}/refresh`)
  data.last_error ? ElMessage.error(data.last_error) : ElMessage.success('已刷新')
  load()
}
async function remove(row: any) {
  await http.delete(`/stores/${row.id}`)
  load()
}
onMounted(async () => {
  load()
  meta.value = (await http.get('/meta')).data
})
</script>

<style scoped>
.shop { display: flex; align-items: center; gap: 10px; }
</style>

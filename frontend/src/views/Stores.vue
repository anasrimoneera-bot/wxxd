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
            <div class="shop"><el-avatar :src="row.logo" :size="32">{{ row.name.slice(0, 1) }}</el-avatar><span>{{ row.name }}</span><el-tooltip v-if="row.shared" :content="`由「${row.owner_name}」分配给你使用：商品、订单、发货、上货都可以操作，店铺设置由所属账号修改`" placement="top"><el-tag size="small" type="warning" effect="plain" class="shared">共享</el-tag></el-tooltip></div>
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
        <el-table-column label="操作" width="270" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="refresh(row)">刷新</el-button>
            <el-button link type="primary" @click="$router.push('/publish')">发布配置</el-button>
            <el-button v-if="!row.shared" link type="primary" @click="openPush(row)">消息推送</el-button>
            <el-popconfirm v-if="!row.shared" title="删除后该店铺的配置将清除，确定？" @confirm="remove(row)">
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

    <el-dialog v-model="pushVisible" :title="`消息推送（买家消息）· ${pushStore?.name || ''}`" width="620px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 12px"
                title="接入后，这个店铺在微信小店后台自带的客服工作台将不能再用，买家消息改在本系统「买家消息」里回复。" />
      <ol class="push-steps">
        <li>微信小店后台 →「开发」→「消息推送」，填写下面的 URL、Token、EncodingAESKey（可点「生成」），消息加密方式选「安全模式」或「明文模式」，数据格式 JSON 或 XML 都可以。</li>
        <li>先在这里点「保存」，再到微信后台点提交（微信会校验这个地址）。</li>
        <li>微信小店后台 →「客服」→ 选择用 API 接入（自研客服）。</li>
      </ol>
      <el-form label-width="130px">
        <el-form-item label="URL（服务器地址）">
          <el-input :model-value="pushUrl" readonly><template #append><el-button @click="copy(pushUrl)">复制</el-button></template></el-input>
        </el-form-item>
        <el-form-item label="Token">
          <el-input v-model="push.token" maxlength="32"><template #append><el-button @click="push.token = rand(32)">生成</el-button></template></el-input>
        </el-form-item>
        <el-form-item label="EncodingAESKey">
          <el-input v-model="push.aes_key" maxlength="43" :placeholder="push.aes_key_set ? '已设置（留空保持不变）' : '43 位，安全模式需要'">
            <template #append><el-button @click="push.aes_key = rand(43)">生成</el-button></template>
          </el-input>
          <div class="muted small">只保存在服务器上（加密）。在微信后台填同样的值。</div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="pushVisible = false">关闭</el-button><el-button type="primary" :loading="pushSaving" @click="savePush">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
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

// 消息推送: the URL / Token / EncodingAESKey that the WeChat back office sends buyer messages with
const pushVisible = ref(false)
const pushSaving = ref(false)
const pushStore = ref<any>(null)
const push = reactive<any>({ path: '', token: '', aes_key: '', aes_key_set: false })
const pushUrl = computed(() => (push.path ? window.location.origin + push.path : ''))
const rand = (n: number) => { const c = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'; const a = new Uint32Array(n); crypto.getRandomValues(a); return Array.from(a, (x) => c[x % c.length]).join('') }
async function openPush(row: any) {
  pushStore.value = row
  const { data } = await http.get(`/stores/${row.id}/push`)
  Object.assign(push, data, { aes_key: '' })
  if (!push.token) push.token = rand(32)
  pushVisible.value = true
}
async function savePush() {
  pushSaving.value = true
  try {
    const { data } = await http.put(`/stores/${pushStore.value.id}/push`, { token: push.token, aes_key: push.aes_key })
    Object.assign(push, data, { aes_key: '' })
    ElMessage.success('已保存，现在可以到微信小店后台提交消息推送配置了')
  } finally { pushSaving.value = false }
}
async function copy(t: string) { await navigator.clipboard.writeText(t); ElMessage.success('已复制') }
</script>

<style scoped>
.push-steps { margin: 0 0 12px; padding-left: 18px; line-height: 1.8; color: #606266; font-size: 13px; }

.shop { display: flex; align-items: center; gap: 10px; }
.shared { margin-left: 6px; padding: 0 4px; }
</style>

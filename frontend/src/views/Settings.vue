<template>
  <div class="card" style="max-width: 820px">
    <div class="card-head"><div class="card-title">系统设置</div><span class="muted">仅平台管理员可见，对所有客户账号生效</span></div>
    <el-divider content-position="left">DeepSeek（AI 改写标题）</el-divider>
    <el-form label-width="120px" v-loading="loading">
      <el-form-item label="API Key">
        <div style="width: 100%">
          <div v-if="s.deepseek_api_key_set" class="current">
            当前：<el-tag type="success">{{ s.deepseek_api_key_masked }}</el-tag>
            <el-popconfirm title="清除后将停止 AI 改写标题，确定？" @confirm="clearKey">
              <template #reference><el-button link type="danger" class="ml">清除</el-button></template>
            </el-popconfirm>
          </div>
          <el-input v-model="newKey" type="password" show-password autocomplete="off"
                    :placeholder="s.deepseek_api_key_set ? '输入新的 Key 以替换（留空则保持不变）' : '输入 DeepSeek API Key（sk-…）'" />
          <div class="muted tip">加密保存，保存后只显示首尾几位。约 30 秒内所有 Worker 自动生效，无需重启。</div>
        </div>
      </el-form-item>
      <el-form-item label="接口地址"><el-input v-model="s.deepseek_base_url" placeholder="https://api.deepseek.com" /></el-form-item>
      <el-form-item label="模型">
        <div style="width: 100%">
          <el-input v-model="s.deepseek_model" placeholder="deepseek-flash" />
          <div class="muted tip">推荐 deepseek-flash（最便宜，改标题足够）。已自动关闭思考模式；批量生成尽量放在空闲时段（工作日 9-12、14-18 以外及周末），价格减半。</div>
        </div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        <el-button :loading="testing" @click="test">测试改写</el-button>
      </el-form-item>
      <el-form-item v-if="result" label="测试结果">
        <div>
          <div class="muted">原标题：{{ result.source }}</div>
          <div>改写后：<b>{{ result.rewritten }}</b></div>
        </div>
      </el-form-item>
    </el-form>

    <el-divider content-position="left">1688 货源（找货源 · 以图搜货）</el-divider>
    <el-form label-width="120px">
      <el-form-item label="1688 AK">
        <div style="width: 100%">
          <div v-if="s.ali1688_ak_set" class="current">
            当前：<el-tag type="success">{{ s.ali1688_ak_masked }}</el-tag>
            <el-popconfirm title="清除后找货源将无法使用，确定？" @confirm="clear1688"><template #reference><el-button link type="danger" class="ml">清除</el-button></template></el-popconfirm>
          </div>
          <el-input v-model="new1688" type="password" show-password autocomplete="off"
                    :placeholder="s.ali1688_ak_set ? '输入新的 AK 以替换（留空则保持不变）' : '粘贴从 clawhub.1688.com 右上角 🔑 复制的 AK'" />
          <div class="muted tip">登录 clawhub.1688.com，点击右上角 🔑 复制 AK。加密保存，只显示首尾几位。</div>
        </div>
      </el-form-item>
      <el-form-item label="渠道码"><el-input v-model="s.ali1688_channel" placeholder="github" style="width: 200px" /><span class="muted ml">一般无需修改</span></el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        <el-button :loading="testing1688" @click="test1688">测试连接</el-button>
      </el-form-item>
      <el-form-item v-if="result1688" label="测试结果">
        <div>找到 {{ result1688.count }} 个货源<span v-for="o in result1688.sample" :key="o.offer_id" class="muted"> · {{ o.title.slice(0, 16) }} ¥{{ o.price }}</span></div>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const s = ref<any>({})
const newKey = ref('')
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const result = ref<any>(null)

async function load() {
  loading.value = true
  try { s.value = (await http.get('/admin/settings')).data } finally { loading.value = false }
}
async function save() {
  saving.value = true
  try {
    s.value = (await http.put('/admin/settings', {
      deepseek_api_key: newKey.value || undefined,
      deepseek_base_url: s.value.deepseek_base_url, deepseek_model: s.value.deepseek_model,
      ali1688_ak: new1688.value || undefined, ali1688_channel: s.value.ali1688_channel,
    })).data
    newKey.value = ''
    new1688.value = ''
    ElMessage.success('已保存')
  } finally { saving.value = false }
}
async function clearKey() {
  s.value = (await http.put('/admin/settings', { clear_deepseek_api_key: true,
    deepseek_base_url: s.value.deepseek_base_url, deepseek_model: s.value.deepseek_model,
    ali1688_channel: s.value.ali1688_channel })).data
  ElMessage.success('已清除')
}
const new1688 = ref('')
const testing1688 = ref(false)
const result1688 = ref<any>(null)
async function clear1688() {
  s.value = (await http.put('/admin/settings', { clear_ali1688_ak: true, deepseek_base_url: s.value.deepseek_base_url,
    deepseek_model: s.value.deepseek_model, ali1688_channel: s.value.ali1688_channel })).data
  ElMessage.success('已清除')
}
async function test1688() {
  testing1688.value = true
  result1688.value = null
  try { result1688.value = (await http.post('/admin/settings/test-1688')).data } finally { testing1688.value = false }
}
async function test() {
  testing.value = true
  result.value = null
  try { result.value = (await http.post('/admin/settings/test-deepseek', {})).data } finally { testing.value = false }
}
onMounted(load)
</script>

<style scoped>
.current { margin-bottom: 8px; }
.ml { margin-left: 8px; }
.tip { margin-top: 6px; line-height: 1.5; }
</style>

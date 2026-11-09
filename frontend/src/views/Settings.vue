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
    })).data
    newKey.value = ''
    ElMessage.success('已保存')
  } finally { saving.value = false }
}
async function clearKey() {
  s.value = (await http.put('/admin/settings', { clear_deepseek_api_key: true,
    deepseek_base_url: s.value.deepseek_base_url, deepseek_model: s.value.deepseek_model })).data
  ElMessage.success('已清除')
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

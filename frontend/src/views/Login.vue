<template>
  <div class="wrap">
    <div class="box">
      <h2>🛒 微信小店助手</h2>
      <!-- Only the very first account (the platform admin) can register; others are created in 用户管理 -->
      <el-tabs v-if="registrationOpen" v-model="mode" stretch>
        <el-tab-pane label="登录" name="login" />
        <el-tab-pane label="注册管理员账号" name="register" />
      </el-tabs>
      <el-form :model="form" @submit.prevent="submit" label-position="top">
        <el-form-item label="用户名"><el-input v-model="form.username" autocomplete="username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password autocomplete="current-password" /></el-form-item>
        <el-form-item v-if="mode === 'register'" label="公司 / 团队名称"><el-input v-model="form.company" /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%">{{ mode === 'login' ? '登录' : '注册并登录' }}</el-button>
      </el-form>
      <p v-if="!registrationOpen" class="hint">没有账号？请联系管理员开通</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { http } from '../api'

const router = useRouter()
const mode = ref('login')
const loading = ref(false)
const form = reactive({ username: '', password: '', company: '' })
const registrationOpen = ref(false)
onMounted(async () => {
  try { registrationOpen.value = (await http.get('/auth/setup')).data.registration_open } catch { registrationOpen.value = false }
})

async function submit() {
  loading.value = true
  try {
    const { data } = await http.post(`/auth/${mode.value}`, form)
    localStorage.setItem('token', data.token)
    localStorage.setItem('user', JSON.stringify(data.user))
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.wrap { height: 100%; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #111a2e, #24406e); }
.box { width: 360px; background: #fff; border-radius: 12px; padding: 28px 32px; }
h2 { text-align: center; margin: 0 0 10px; }
.hint { text-align: center; color: #909399; font-size: 12px; margin: 14px 0 0; }
</style>

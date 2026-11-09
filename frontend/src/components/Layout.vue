<template>
  <div class="layout">
    <aside class="side">
      <div class="logo">🛒 微信小店助手</div>
      <el-menu :default-active="route.path" router background-color="#111a2e" text-color="#cbd5e1"
               active-text-color="#fff" :default-openeds="['wx', 'cloud']">
        <el-menu-item index="/"><el-icon><HomeFilled /></el-icon>首页</el-menu-item>
        <el-sub-menu index="wx">
          <template #title><el-icon><ShoppingCart /></el-icon>微信小助手</template>
          <el-menu-item index="/stores">店铺管理</el-menu-item>
          <el-menu-item index="/library">商品云库</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="cloud">
          <template #title><el-icon><Promotion /></el-icon>云发布工作台</template>
          <el-menu-item index="/publish">发布配置</el-menu-item>
          <el-menu-item index="/tasks">任务列表</el-menu-item>
        </el-sub-menu>
        <el-menu-item v-if="user.role === 'owner'" index="/subaccounts"><el-icon><User /></el-icon>子账号管理</el-menu-item>
        <el-menu-item v-if="user.is_admin" index="/settings"><el-icon><Setting /></el-icon>系统设置</el-menu-item>
      </el-menu>
    </aside>
    <section class="main">
      <header class="top">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item to="/">首页</el-breadcrumb-item>
          <el-breadcrumb-item v-if="route.meta.title && route.path !== '/'">{{ route.meta.title }}</el-breadcrumb-item>
        </el-breadcrumb>
        <el-dropdown @command="logout">
          <span class="user">{{ user.display_name || user.username }}
            <el-tag size="small" class="ml">{{ user.role === 'owner' ? '主账号' : '子账号' }}</el-tag></span>
          <template #dropdown><el-dropdown-menu><el-dropdown-item command="out">退出登录</el-dropdown-item></el-dropdown-menu></template>
        </el-dropdown>
      </header>
      <main class="content"><router-view /></main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { http } from '../api'

const route = useRoute()
const router = useRouter()
const user = reactive<any>(JSON.parse(localStorage.getItem('user') || '{}'))

onMounted(async () => {
  const { data } = await http.get('/auth/me')
  Object.assign(user, data)
  localStorage.setItem('user', JSON.stringify(data))
})

function logout() {
  localStorage.clear()
  router.push('/login')
}
</script>

<style scoped>
.layout { display: flex; height: 100%; }
.side { width: 220px; background: #111a2e; flex-shrink: 0; overflow-y: auto; }
.side .el-menu { border-right: none; }
.logo { color: #fff; font-weight: 700; font-size: 17px; padding: 20px; text-align: center; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.top { height: 56px; background: #fff; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; border-bottom: 1px solid #eef0f4; }
.user { cursor: pointer; display: flex; align-items: center; }
.ml { margin-left: 6px; }
.content { flex: 1; overflow: auto; padding: 14px; }
</style>

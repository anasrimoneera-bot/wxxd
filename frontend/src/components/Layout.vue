<template>
  <div class="layout">
    <aside class="side">
      <div class="logo">🛒 微信小店助手</div>
      <el-menu :default-active="route.path" router background-color="#111a2e" text-color="#cbd5e1"
               active-text-color="#fff" :default-openeds="['wx', 'cloud', 'admin']">
        <el-menu-item index="/"><el-icon><HomeFilled /></el-icon>首页</el-menu-item>
        <el-sub-menu index="wx">
          <template #title><el-icon><ShoppingCart /></el-icon>微信小助手<span v-if="unread.count" class="dot" title="有新的买家消息" /></template>
          <el-menu-item index="/stores">店铺管理</el-menu-item>
          <el-menu-item index="/products">商品管理</el-menu-item>
          <el-menu-item index="/orders">订单管理</el-menu-item>
          <el-menu-item index="/messages">买家消息<el-badge v-if="unread.count" :value="unread.count" :max="99" class="nav-badge" /></el-menu-item>
          <el-menu-item index="/library">商品云库</el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="cloud">
          <template #title><el-icon><Promotion /></el-icon>云发布工作台</template>
          <el-menu-item index="/publish">发布配置</el-menu-item>
          <el-menu-item index="/tasks">任务列表</el-menu-item>
          <el-menu-item index="/categories">一键申请类目</el-menu-item>
        </el-sub-menu>
        <el-menu-item v-if="user.role === 'owner'" index="/subaccounts"><el-icon><User /></el-icon>子账号管理</el-menu-item>
        <el-sub-menu v-if="user.is_admin" index="admin">
          <template #title><el-icon><Setting /></el-icon>平台管理</template>
          <el-menu-item index="/customers">用户管理</el-menu-item>
          <el-menu-item index="/settings">系统设置</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </aside>
    <section class="main">
      <header class="top">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item to="/">首页</el-breadcrumb-item>
          <el-breadcrumb-item v-if="route.meta.title && route.path !== '/'">{{ route.meta.title }}</el-breadcrumb-item>
        </el-breadcrumb>
        <span v-if="!user.is_admin && user.expires_at" class="exp">会员到期：{{ user.expires_at }}</span>
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
import { onMounted, onUnmounted, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElNotification } from 'element-plus'
import { http } from '../api'

const route = useRoute()
const router = useRouter()
const user = reactive<any>(JSON.parse(localStorage.getItem('user') || '{}'))

onMounted(async () => {
  const { data } = await http.get('/auth/me')
  Object.assign(user, data)
  localStorage.setItem('user', JSON.stringify(data))
})

/* 新买家消息: red count on 买家消息 (and a dot on its group), the count in the tab title, and a pop-up
 * (a system notification as well when this tab is in the background) for each new message */
const unread = reactive<{ count: number; latest: any }>({ count: 0, latest: null })
let seen = Number(localStorage.getItem('msgSeen') || 0)
let timer: any = null
const baseTitle = document.title.replace(/^\(\d+\+?\) /, '')
async function pollUnread() {
  try {
    const { data } = await http.get('/messages/unread', { headers: { 'X-Silent': '1' } })
    Object.assign(unread, data)
    if (route.path !== '/messages') document.title = data.count ? `(${data.count > 99 ? '99+' : data.count}) ${baseTitle}` : baseTitle
    const m = data.latest
    if (m && m.id > seen) {
      const first = !seen  // first visit: just remember, no pop-up for old messages
      seen = m.id
      localStorage.setItem('msgSeen', String(seen))
      if (first) return
      const open = () => router.push({ path: '/messages', query: { store_id: m.store_id, openid: m.openid } })
      if (route.path !== '/messages') {
        ElNotification({ title: `新的买家消息 · ${m.store}`, message: m.text || '（新消息）', type: 'warning', duration: 8000,
          position: 'bottom-right', onClick: open })
      }
      if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
        const n = new Notification(`新的买家消息 · ${m.store}`, { body: m.text || '', tag: 'buyer-msg' })
        n.onclick = () => { window.focus(); open(); n.close() }
      }
    }
  } catch (e) { /* offline or logged out: try again next time */ }
}
onMounted(() => {
  pollUnread()
  timer = setInterval(pollUnread, 20000)
  if ('Notification' in window && Notification.permission === 'default') {
    const ask = () => { Notification.requestPermission().catch(() => {}); window.removeEventListener('click', ask) }
    window.addEventListener('click', ask)  // browsers only ask after a click on the page
  }
})
onUnmounted(() => clearInterval(timer))

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
.exp { margin-left: auto; margin-right: 16px; color: #6b7280; font-size: 13px; }
.user { cursor: pointer; display: flex; align-items: center; }
.ml { margin-left: 6px; }
.content { flex: 1; overflow: auto; padding: 14px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #f56c6c; margin-left: 6px; vertical-align: middle; }
.nav-badge { margin-left: 8px; line-height: 1; }
.nav-badge :deep(.el-badge__content) { border: none; }
</style>

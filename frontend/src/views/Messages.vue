<template>
  <!-- 买家消息: the buyer messages of every store, answered from one place -->
  <div class="msg-page">
    <div class="card conv-list">
      <div class="conv-head">
        <b>买家消息</b>
        <el-select v-model="storeId" placeholder="全部店铺" clearable size="small" style="width: 140px" @change="loadConvs">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </div>
      <el-alert v-if="unsetStores.length" type="warning" :closable="false" show-icon class="setup-tip">
        <template #title>{{ unsetStores.length }} 个店铺还没接入消息推送</template>
        在「店铺管理 → 消息推送」里设置后，买家消息才会到这里。
      </el-alert>
      <div v-if="!convs.length" class="muted empty">暂无买家消息</div>
      <div v-for="c in convs" :key="c.store_id + c.openid" class="conv" :class="{ on: cur && cur.store_id === c.store_id && cur.openid === c.openid }" @click="openConv(c)">
        <el-badge :value="c.unread" :hidden="!c.unread" class="conv-badge"><div class="avatar">{{ c.store_name.slice(0, 1) }}</div></el-badge>
        <div class="conv-main">
          <div class="conv-top"><span class="conv-name">买家 {{ c.openid.slice(-6) }}</span><span class="muted small">{{ shortTime(c.last?.created_at) }}</span></div>
          <div class="muted small conv-store">{{ c.store_name }}</div>
          <div class="conv-last">{{ c.last?.direction === 'out' ? '我：' : '' }}{{ c.last?.content }}</div>
        </div>
      </div>
    </div>
    <div class="card chat">
      <template v-if="cur">
        <div class="chat-head"><b>买家 {{ cur.openid.slice(-6) }}</b><span class="muted small">{{ cur.store_name }} · {{ cur.openid }}</span></div>
        <div ref="box" class="chat-body">
          <div v-for="m in msgs" :key="m.id" class="bubble-row" :class="m.direction">
            <div class="bubble">{{ m.content }}</div>
            <div class="muted small">{{ m.direction === 'out' ? (m.sender || '我') + ' · ' : '' }}{{ m.created_at }}</div>
          </div>
        </div>
        <div class="chat-input">
          <div v-if="!state.can_reply" class="muted small warn">{{ state.reason }}</div>
          <div v-else class="muted small">还能回复 {{ state.left }} 条（{{ state.deadline }} 前；买家再发消息后重新计算）</div>
          <el-input v-model="text" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="!state.can_reply"
                    placeholder="输入回复，Ctrl + Enter 发送" @keydown.ctrl.enter="send" />
          <div class="send-row">
            <el-button v-for="q in quick" :key="q" size="small" :disabled="!state.can_reply" @click="text = q">{{ q.slice(0, 8) }}…</el-button>
            <el-button type="primary" :loading="sending" :disabled="!state.can_reply || !text.trim()" @click="send">发送</el-button>
          </div>
        </div>
      </template>
      <div v-else class="muted empty">在左边选择一个买家</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const storeId = ref<number | undefined>()
const stores = ref<any[]>([])
const convs = ref<any[]>([])
const cur = ref<any>(null)
const msgs = ref<any[]>([])
const state = ref<any>({ can_reply: false })
const text = ref('')
const sending = ref(false)
const box = ref<HTMLElement>()
const unsetStores = computed(() => stores.value.filter((s) => !s.push_set))
const quick = ['您好，在的，请问有什么可以帮您？', '亲，已经安排发货了，请留意物流信息～', '亲，麻烦提供一下订单号，我帮您查一下。']

const shortTime = (t?: string) => (t ? (t.slice(0, 10) === new Date().toISOString().slice(0, 10) ? t.slice(11, 16) : t.slice(5, 10)) : '')

async function loadConvs() {
  const { data } = await http.get('/messages/conversations', { params: { store_id: storeId.value } })
  convs.value = data.items; stores.value = data.stores
}
async function loadMsgs(scroll = true) {
  if (!cur.value) return
  const { data } = await http.get('/messages', { params: { store_id: cur.value.store_id, openid: cur.value.openid } })
  const grew = data.items.length !== msgs.value.length
  msgs.value = data.items
  state.value = data
  if (scroll || grew) { await nextTick(); if (box.value) box.value.scrollTop = box.value.scrollHeight }
}
async function openConv(c: any) { cur.value = c; msgs.value = []; text.value = ''; await loadMsgs(); c.unread = 0 }
async function send() {
  if (!text.value.trim() || !state.value.can_reply) return
  sending.value = true
  try {
    const { data } = await http.post('/messages/send', { store_id: cur.value.store_id, openid: cur.value.openid, text: text.value.trim() })
    msgs.value.push(data.message); state.value = { ...state.value, ...data }; text.value = ''
    await nextTick(); if (box.value) box.value.scrollTop = box.value.scrollHeight
    loadConvs()
  } catch (e: any) { if (!e?.response) ElMessage.error('发送失败') } finally { sending.value = false }
}
let timer: number | undefined
onMounted(() => { loadConvs(); timer = window.setInterval(() => { loadConvs(); loadMsgs(false) }, 15000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.msg-page { display: flex; gap: 12px; height: calc(100vh - 110px); }
.card { background: #fff; border-radius: 8px; }
.conv-list { width: 320px; flex-shrink: 0; overflow-y: auto; }
.conv-head { display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid #f0f0f0; position: sticky; top: 0; background: #fff; z-index: 1; }
.setup-tip { margin: 8px; width: auto; }
.conv { display: flex; gap: 10px; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid #f7f7f7; }
.conv:hover { background: #f5f7fa; } .conv.on { background: #ecf5ff; }
.avatar { width: 40px; height: 40px; border-radius: 50%; background: #409eff; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 600; }
.conv-main { flex: 1; min-width: 0; }
.conv-top { display: flex; justify-content: space-between; }
.conv-name { font-weight: 600; }
.conv-store { margin: 1px 0; }
.conv-last { color: #606266; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-head { padding: 12px 16px; border-bottom: 1px solid #f0f0f0; display: flex; gap: 10px; align-items: baseline; }
.chat-body { flex: 1; overflow-y: auto; padding: 16px; background: #f7f8fa; }
.bubble-row { display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 12px; }
.bubble-row.out { align-items: flex-end; }
.bubble { max-width: 70%; padding: 8px 12px; border-radius: 8px; background: #fff; white-space: pre-wrap; word-break: break-word; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.bubble-row.out .bubble { background: #95ec69; }
.chat-input { padding: 10px 16px; border-top: 1px solid #f0f0f0; }
.send-row { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; }
.warn { color: #e6a23c; }
.empty { padding: 40px; text-align: center; }
.muted { color: #909399; } .small { font-size: 12px; }
</style>

<template>
  <!-- 买家消息: the buyer messages of every store, answered from one place (by people or AI 客服) -->
  <div class="msg-page">
    <div class="card conv-list">
      <div class="conv-head">
        <b>买家消息</b>
        <el-select v-model="storeId" placeholder="全部店铺" clearable size="small" style="width: 120px" @change="loadConvs">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-button size="small" type="success" plain @click="openAi()">AI 客服</el-button>
        <el-tooltip content="买家消息由微信实时推送到网站，这个页面每 5 秒自动刷新，有新消息会响一声；点这里立即刷新。" placement="bottom">
          <el-button size="small" icon="Refresh" :loading="refreshing" @click="refresh" />
        </el-tooltip>
      </div>
      <div class="sub-head">
        <span class="muted small">{{ lastAt ? `已自动刷新 · ${lastAt}` : '' }}</span>
        <el-checkbox v-model="onlyHuman" size="small">只看需人工{{ needHumanTotal ? `（${needHumanTotal}）` : '' }}</el-checkbox>
      </div>
      <el-alert v-if="unsetStores.length" type="warning" :closable="false" show-icon class="setup-tip">
        <template #title>{{ unsetStores.length }} 个店铺还没接入消息推送</template>
        在「店铺管理 → 消息推送」里设置后，买家消息才会到这里。
      </el-alert>
      <el-alert v-if="eventStores.length" type="error" :closable="false" show-icon class="setup-tip">
        <template #title>{{ eventStores.map((s) => s.name).join('、') }}：只收到了「买家评价」，收不到聊天消息</template>
        店铺的客服还在「客服工作台」模式，买家的聊天只进小店自带的客服工作台。请到
        <b>微信小店后台 → 店铺管理 → 客服管理 → 客服设置 → 接入自研客服</b>，由超级管理员扫码切换后，买家消息才会实时到这里，也才能在这里回复。
        切换后小店自带的欢迎语、自动回复不再生效，可以开启「AI 客服」代替。
      </el-alert>
      <el-alert v-if="aiStoresWithoutKey" type="warning" :closable="false" show-icon class="setup-tip"
                title="AI 客服已开启，但系统设置里还没有 DeepSeek API Key，AI 不会回复" />
      <div v-if="!shownConvs.length" class="muted empty">{{ onlyHuman ? '没有需要人工处理的会话' : '暂无买家消息' }}</div>
      <div v-for="c in shownConvs" :key="c.store_id + c.openid" class="conv" :class="{ on: cur && cur.store_id === c.store_id && cur.openid === c.openid }" @click="openConv(c)">
        <el-badge :value="c.unread" :hidden="!c.unread" class="conv-badge"><div class="avatar">{{ c.store_name.slice(0, 1) }}</div></el-badge>
        <div class="conv-main">
          <div class="conv-top"><span class="conv-name">买家 {{ c.openid.slice(-6) }}</span><span class="muted small">{{ shortTime(c.last?.created_at) }}</span></div>
          <div class="muted small conv-store">{{ c.store_name }}
            <el-tag v-if="c.need_human" type="danger" size="small" effect="dark" class="tag">需人工</el-tag>
            <el-tag v-if="c.drafts" type="success" size="small" effect="plain" class="tag">AI 建议</el-tag>
          </div>
          <div class="conv-last">{{ lastText(c.last) }}</div>
        </div>
      </div>
    </div>
    <div class="card chat">
      <template v-if="cur">
        <div class="chat-head">
          <b>买家 {{ cur.openid.slice(-6) }}</b><span class="muted small">{{ cur.store_name }} · {{ cur.openid }}</span>
          <el-tag size="small" :type="aiModeOf(cur.store_id) === 'auto' ? 'success' : aiModeOf(cur.store_id) === 'suggest' ? 'warning' : 'info'"
                  effect="plain" class="ai-mode" @click="openAi(cur.store_id)">AI 客服：{{ AI_MODES[aiModeOf(cur.store_id)] }}</el-tag>
        </div>
        <div ref="box" class="chat-body">
          <template v-for="m in msgs" :key="m.id">
            <div v-if="m.msg_type === 'evaluate'" class="sys-row"><span>{{ m.content }} · {{ m.created_at }}</span></div>
            <div v-else-if="m.direction === 'draft'" class="bubble-row out">
              <div class="bubble draft" :class="{ err: m.msg_type === 'error' }">
                <div class="draft-label">{{ m.msg_type === 'error' ? 'AI 客服' : (m.sender || 'AI客服') + ' · 建议回复（还没发送）' }}</div>{{ m.content }}
              </div>
              <div v-if="m.msg_type !== 'error'" class="draft-ops">
                <el-button link type="primary" size="small" :disabled="!state.can_reply" @click="sendDraft(m)">直接发送</el-button>
                <el-button link size="small" @click="text = m.content">编辑后发送</el-button>
                <el-button link type="danger" size="small" @click="dropDraft(m)">删除</el-button>
              </div>
              <div v-else class="draft-ops"><el-button link type="danger" size="small" @click="dropDraft(m)">知道了</el-button></div>
            </div>
            <div v-else class="bubble-row" :class="m.direction">
              <div class="bubble" :class="{ ai: m.sender === 'AI客服' }">{{ m.content }}</div>
              <div class="muted small">
                <el-tag v-if="m.need_human" type="danger" size="small" class="tag">需人工</el-tag>
                {{ m.direction === 'out' ? (m.sender || '我') + ' · ' : '' }}{{ m.created_at }}
              </div>
            </div>
          </template>
        </div>
        <div class="chat-input">
          <div v-if="!state.can_reply" class="muted small warn">{{ state.reason }}</div>
          <div v-else class="muted small">还能回复 {{ state.left }} 条（{{ state.deadline }} 前；买家再发消息后重新计算）</div>
          <el-input v-model="text" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="!state.can_reply"
                    placeholder="输入回复，Ctrl + Enter 发送" @keydown.ctrl.enter="send" />
          <div class="send-row">
            <el-button v-for="q in quick" :key="q" size="small" :disabled="!state.can_reply" @click="text = q">{{ q.slice(0, 8) }}…</el-button>
            <el-button size="small" type="success" plain :loading="suggesting" :disabled="!state.can_reply" @click="suggest">AI 帮我写</el-button>
            <el-button type="primary" :loading="sending" :disabled="!state.can_reply || !text.trim()" @click="send">发送</el-button>
          </div>
        </div>
      </template>
      <div v-else class="muted empty">在左边选择一个买家</div>
    </div>

    <!-- AI 客服 settings of a store -->
    <el-dialog v-model="aiVisible" title="AI 客服" width="640px">
      <el-form label-width="96px">
        <el-form-item label="店铺">
          <el-select v-model="ai.store_id" style="width: 100%" @change="loadAi">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-alert v-if="!ai.key_set" type="warning" :closable="false" show-icon style="margin-bottom: 10px"
                  title="还没有 DeepSeek API Key：请平台管理员在「系统设置」里填写（和 AI 生成标题用同一个）" />
        <el-form-item label="回复方式">
          <el-radio-group v-model="ai.mode">
            <el-radio value="off">关闭</el-radio>
            <el-radio value="suggest">只给建议（人工确认后发送）</el-radio>
            <el-radio value="auto">自动回复</el-radio>
          </el-radio-group>
          <div class="muted small" style="line-height: 1.6; margin-top: 4px">
            买家发消息后几秒内，AI 根据聊天记录、这位买家在本店的订单（状态、物流单号）、买家发来的商品 / 订单卡片和下面的店铺资料回复。
            每条买家消息 AI 只用 1 次回复机会（微信规定买家每发一次消息最多回复 5 条、48 小时内）。
            退款、退货、投诉、质量问题、资料里没有的问题，AI 会先安抚并标记<b>「需人工」</b>，请及时处理。
          </div>
        </el-form-item>
        <el-form-item label="店铺资料">
          <el-input v-model="ai.kb" type="textarea" :rows="10" maxlength="5000" show-word-limit :placeholder="KB_EXAMPLE" />
          <div class="muted small">写得越清楚，AI 回答越准。没写的内容 AI 不会编，会说让同事核实。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="aiVisible = false">取消</el-button>
        <el-button type="primary" :loading="aiSaving" @click="saveAi">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
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
const onlyHuman = ref(false)
const needHumanTotal = ref(0)
const aiKeySet = ref(true)
const unsetStores = computed(() => stores.value.filter((s) => !s.push_set))
const eventStores = computed(() => stores.value.filter((s) => s.only_events))
const aiStoresWithoutKey = computed(() => !aiKeySet.value && stores.value.some((s) => s.ai_reply_mode !== 'off'))
const shownConvs = computed(() => (onlyHuman.value ? convs.value.filter((c) => c.need_human) : convs.value))
const quick = ['您好，在的，请问有什么可以帮您？', '亲，已经安排发货了，请留意物流信息～', '亲，麻烦提供一下订单号，我帮您查一下。']
const AI_MODES: Record<string, string> = { off: '关闭', suggest: '只给建议', auto: '自动回复' }
const aiModeOf = (sid: number) => stores.value.find((s) => s.id === sid)?.ai_reply_mode || 'off'
const KB_EXAMPLE = `例如：
发货：下单后 48 小时内发货，默认中通 / 圆通，偏远地区（新疆、西藏）不发货。
售后：支持 7 天无理由退换，质量问题我们承担运费。
尺码：衣服偏小，建议拍大一码。
优惠：满 2 件包邮。
营业时间：每天 9:00–22:00，其他时间留言第二天处理。`

const shortTime = (t?: string) => (t ? (t.slice(0, 10) === new Date().toISOString().slice(0, 10) ? t.slice(11, 16) : t.slice(5, 10)) : '')
const lastText = (m: any) => (!m ? '' : m.direction === 'draft' ? `[AI 建议] ${m.content}` : (m.direction === 'out' ? (m.sender === 'AI客服' ? 'AI：' : '我：') : '') + m.content)

/* a short beep when new buyer messages come in (the page may be in another tab) */
let lastUnread = -1
function beep() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)()
    const o = ctx.createOscillator(), g = ctx.createGain()
    o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(ctx.destination)
    o.start(); o.stop(ctx.currentTime + 0.25)
  } catch { /* no sound */ }
}
const baseTitle = document.title

const refreshing = ref(false)
const lastAt = ref('')
async function loadConvs() {
  const { data } = await http.get('/messages/conversations', { params: { store_id: storeId.value } })
  convs.value = data.items; stores.value = data.stores
  needHumanTotal.value = data.need_human_total || 0
  aiKeySet.value = data.ai_key_set !== false
  if (lastUnread >= 0 && data.unread_total > lastUnread) beep()
  lastUnread = data.unread_total
  document.title = data.unread_total ? `(${data.unread_total}) 买家消息` : baseTitle
  lastAt.value = new Date().toTimeString().slice(0, 8)
}
async function refresh() {
  refreshing.value = true
  try { await Promise.all([loadConvs(), loadMsgs(false)]) } finally { refreshing.value = false }
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
async function sendText(t: string) {
  sending.value = true
  try {
    await http.post('/messages/send', { store_id: cur.value.store_id, openid: cur.value.openid, text: t })
    text.value = ''
    await loadMsgs(); loadConvs()
  } catch (e: any) { if (!e?.response) ElMessage.error('发送失败') } finally { sending.value = false }
}
async function send() {
  if (!text.value.trim() || !state.value.can_reply) return
  await sendText(text.value.trim())
}
const sendDraft = (m: any) => sendText(m.content)
async function dropDraft(m: any) { await http.delete(`/messages/draft/${m.id}`); loadMsgs(false); loadConvs() }
const suggesting = ref(false)
async function suggest() {
  suggesting.value = true
  try {
    const { data } = await http.post('/messages/suggest', { store_id: cur.value.store_id, openid: cur.value.openid })
    text.value = data.reply
    if (data.need_human) ElMessage.warning('AI 认为这个问题需要人工确认（退款 / 投诉 / 资料里没有的信息），请核对后再发送')
  } finally { suggesting.value = false }
}

// AI 客服 settings
const aiVisible = ref(false)
const aiSaving = ref(false)
const ai = reactive<any>({ store_id: undefined, mode: 'off', kb: '', key_set: true })
async function loadAi() { Object.assign(ai, (await http.get(`/stores/${ai.store_id}/ai-reply`)).data) }
async function openAi(sid?: number) {
  ai.store_id = sid || cur.value?.store_id || storeId.value || stores.value[0]?.id
  if (!ai.store_id) return ElMessage.warning('还没有店铺')
  await loadAi(); aiVisible.value = true
}
async function saveAi() {
  aiSaving.value = true
  try {
    await http.put(`/stores/${ai.store_id}/ai-reply`, { mode: ai.mode, kb: ai.kb })
    ElMessage.success('已保存'); aiVisible.value = false; loadConvs()
  } finally { aiSaving.value = false }
}

let timer: number | undefined
onMounted(() => { loadConvs(); timer = window.setInterval(() => { loadConvs(); if (!document.hidden) loadMsgs(false) }, 5000) })
onUnmounted(() => { clearInterval(timer); document.title = baseTitle })
</script>

<style scoped>
.msg-page { display: flex; gap: 12px; height: calc(100vh - 110px); }
.card { background: #fff; border-radius: 8px; }
.conv-list { width: 340px; flex-shrink: 0; overflow-y: auto; }
.conv-head { display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 12px; border-bottom: 1px solid #f0f0f0; position: sticky; top: 0; background: #fff; z-index: 1; }
.sub-head { display: flex; justify-content: space-between; align-items: center; padding: 2px 12px; }
.setup-tip { margin: 8px; width: auto; line-height: 1.6; }
.conv { display: flex; gap: 10px; padding: 10px 12px; cursor: pointer; border-bottom: 1px solid #f7f7f7; }
.conv:hover { background: #f5f7fa; } .conv.on { background: #ecf5ff; }
.avatar { width: 40px; height: 40px; border-radius: 50%; background: #409eff; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 600; }
.conv-main { flex: 1; min-width: 0; }
.conv-top { display: flex; justify-content: space-between; }
.conv-name { font-weight: 600; }
.conv-store { margin: 1px 0; }
.tag { margin-left: 4px; }
.conv-last { color: #606266; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-head { padding: 12px 16px; border-bottom: 1px solid #f0f0f0; display: flex; gap: 10px; align-items: baseline; }
.ai-mode { margin-left: auto; cursor: pointer; }
.chat-body { flex: 1; overflow-y: auto; padding: 16px; background: #f7f8fa; }
.bubble-row { display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 12px; }
.bubble-row.out { align-items: flex-end; }
.bubble { max-width: 70%; padding: 8px 12px; border-radius: 8px; background: #fff; white-space: pre-wrap; word-break: break-word; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
.bubble-row.out .bubble { background: #95ec69; }
.bubble-row.out .bubble.ai { background: #d9f7be; }
.bubble.draft { background: #fff !important; border: 1px dashed #67c23a; color: #303133; }
.bubble.draft.err { border-color: #f56c6c; color: #f56c6c; }
.draft-label { font-size: 12px; color: #67c23a; margin-bottom: 4px; }
.draft-ops { margin-top: 2px; }
.sys-row { text-align: center; margin: 8px 0 14px; }
.sys-row span { font-size: 12px; color: #909399; background: #eef0f3; padding: 3px 10px; border-radius: 10px; }
.chat-input { padding: 10px 16px; border-top: 1px solid #f0f0f0; }
.send-row { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.warn { color: #e6a23c; }
.empty { padding: 40px; text-align: center; }
.muted { color: #909399; } .small { font-size: 12px; }
</style>

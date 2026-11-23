<template>
  <el-button v-if="isAdmin" plain icon="DataAnalysis" @click="open">找货源统计</el-button>
  <el-dialog v-model="visible" title="找货源统计（1688 货源助手）" width="960px" append-to-body>
    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px">
      <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始" end-placeholder="结束"
                      :clearable="false" style="width: 260px" @change="load" />
      <el-button size="small" @click="range(0)">今天</el-button>
      <el-button size="small" @click="range(6)">近7天</el-button>
      <el-button size="small" @click="range(29)">近30天</el-button>
      <span class="muted">合计 {{ stats?.total ?? 0 }} 个 · 今天 {{ stats?.by_day?.[stats?.today] ?? 0 }} 个</span>
    </div>
    <el-table v-loading="loading" :data="stats?.users || []" border max-height="460" empty-text="这段时间没有找货源记录">
      <el-table-column label="账号" min-width="150" fixed>
        <template #default="{ row }">
          <div style="font-weight: 600">{{ row.display_name || row.username }}</div>
          <div class="muted">{{ row.username }}{{ row.role === 'operator' ? ' · 子账号' : '' }}</div>
        </template>
      </el-table-column>
      <el-table-column label="今天" width="70" align="center"><template #default="{ row }"><b>{{ row.today }}</b></template></el-table-column>
      <el-table-column label="合计" width="150" align="center">
        <template #default="{ row }">
          <el-link type="primary" :underline="false" @click="openLogs(row, '')"><b>{{ row.total }}</b></el-link>
          <div class="muted" style="font-size: 12px">设货源 {{ row['设货源'] }} · 有货源 {{ row['有货源'] }} · 跳过 {{ row['跳过'] }}</div>
        </template>
      </el-table-column>
      <el-table-column v-for="d in stats?.days || []" :key="d" :label="d.slice(5)" width="64" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.days[d]" :content="`设货源 ${row.days[d]['设货源']} · 有货源 ${row.days[d]['有货源']} · 跳过 ${row.days[d]['跳过']}`" placement="top">
            <el-link type="primary" :underline="false" @click="openLogs(row, d)">{{ row.days[d].total }}</el-link>
          </el-tooltip>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
    </el-table>
    <p class="muted" style="font-size: 12px">统计每个账号在 1688 货源助手插件里处理的商品：「设货源」（选定货源并设好价格）、「有货源」（第一阶段确认有同款）、「跳过」（无货源）。
      包括所有客户的主账号和子账号（只有平台管理员能看到这个统计）；同一个商品处理几次就算几次。</p>
    <template v-if="logUser">
      <div style="font-weight: 600; margin: 8px 0 4px">{{ logUser.display_name || logUser.username }} · {{ logDay || '全部日期' }} 的明细（{{ logs.length }}）</div>
      <el-table :data="logs" border size="small" max-height="300">
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column label="结果" width="80"><template #default="{ row }">
          <el-tag size="small" :type="row.action === '跳过' ? 'info' : row.action === '设货源' ? 'success' : 'warning'">{{ row.action }}</el-tag></template></el-table-column>
        <el-table-column prop="store" label="店铺" min-width="100" />
        <el-table-column prop="wx_product_id" label="商品ID" min-width="150" />
        <el-table-column prop="title" label="商品" min-width="200" show-overflow-tooltip />
        <el-table-column label="1688 货源" min-width="120"><template #default="{ row }">
          <el-link v-if="row.offer_id" type="primary" :href="`https://detail.1688.com/offer/${row.offer_id}.html`" target="_blank">{{ row.offer_id }}</el-link></template></el-table-column>
      </el-table>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { http } from '../api'

const isAdmin = (() => { try { return !!JSON.parse(localStorage.getItem('user') || '{}').is_admin } catch { return false } })()
const visible = ref(false)
const loading = ref(false)
const stats = ref<any>(null)
const dates = ref<string[]>([])
const logs = ref<any[]>([])
const logUser = ref<any>(null)
const logDay = ref('')
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
function range(back: number) {
  const e = new Date(), s = new Date(); s.setDate(e.getDate() - back)
  dates.value = [ymd(s), ymd(e)]
  load()
}
async function load() {
  loading.value = true; logUser.value = null
  try {
    const [start, end] = dates.value || []
    stats.value = (await http.get('/ext/sourcing-stats', { params: { start, end } })).data
  } finally { loading.value = false }
}
function open() { visible.value = true; range(6) }
async function openLogs(row: any, day: string) {
  logUser.value = row; logDay.value = day
  const [start, end] = dates.value || []
  logs.value = (await http.get('/ext/sourcing-logs', { params: day ? { user_id: row.user_id, date: day } : { user_id: row.user_id, start, end } })).data
}
</script>

<template>
  <div>
    <div class="card">
      <el-form :inline="true">
        <el-form-item label="店铺名称"><el-input v-model="q.store_name" placeholder="请输入店铺名称" clearable /></el-form-item>
        <el-form-item label="任务状态">
          <el-select v-model="q.status" placeholder="全部状态" clearable style="width: 160px">
            <el-option v-for="(n, k) in statusNames" :key="k" :label="n" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" icon="Search" @click="q.page = 1; load()">查询</el-button><el-button icon="Refresh" @click="reset">重置</el-button></el-form-item>
      </el-form>
    </div>
    <div class="card">
      <div class="card-head"><div class="card-title">发布任务列表</div><span class="muted">任务由独立 Worker 按商品领取并执行，页面每 5 秒自动刷新</span></div>
      <el-table :data="items" border>
        <el-table-column label="任务名称" min-width="200"><template #default="{ row }"><div class="ellipsis" :title="row.name">{{ row.name }}</div></template></el-table-column>
        <el-table-column prop="store_name" label="店铺" min-width="110" />
        <el-table-column prop="template_name" label="上货模板" min-width="120" />
        <el-table-column label="执行 Worker" width="120"><template #default="{ row }">{{ row.worker || '-' }}</template></el-table-column>
        <el-table-column prop="scope" label="商品范围" min-width="140" />
        <el-table-column label="进度" min-width="260">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :stroke-width="8" />
            <div class="counts">
              <el-tag type="success" size="small">成功 {{ row.success }}</el-tag>
              <el-tag type="danger" size="small">失败 {{ row.failed }}</el-tag>
              <el-tag type="info" size="small">剩余 {{ row.remaining }}</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center"><template #default="{ row }"><el-tag :type="statusType[row.status]">{{ row.status_name }}</el-tag></template></el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160" />
        <el-table-column label="开始时间" width="160"><template #default="{ row }">{{ row.started_at || '-' }}</template></el-table-column>
        <el-table-column label="结束时间" width="160"><template #default="{ row }">{{ row.finished_at || '-' }}</template></el-table-column>
        <el-table-column label="操作" width="120" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button v-if="row.failed && ['success', 'partial', 'failed'].includes(row.status)" link type="warning" @click="retry(row)">重试失败</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager"><el-pagination v-model:current-page="q.page" :page-size="q.size" :total="total" layout="total, prev, pager, next" @change="load" /></div>
    </div>

    <el-dialog v-model="detailVisible" width="1300px" top="6vh" @closed="detailTask = null">
      <template #header>
        <div class="card-head" style="margin: 0; padding-right: 30px">
          <span class="card-title">{{ detailTask?.name }} - 商品执行详情</span>
          <div>
            <el-select v-model="itemStatus" placeholder="全部状态" clearable size="small" style="width: 120px" @change="itemPage = 1; loadItems()">
              <el-option v-for="(n, k) in itemStatusNames" :key="k" :label="n" :value="k" />
            </el-select>
            <el-button size="small" icon="Download" class="ml" @click="exportItems">导出表格</el-button>
            <el-button size="small" icon="Refresh" circle @click="loadItems" />
          </div>
        </div>
      </template>
      <el-table :data="detailItems" border height="60vh">
        <el-table-column label="商品" min-width="280">
          <template #default="{ row }"><div class="prod"><el-image :src="row.cover" style="width: 44px; height: 44px; flex-shrink: 0" fit="cover" /><span class="two">{{ row.title }}</span></div></template>
        </el-table-column>
        <el-table-column prop="library_product_id" label="云库商品 ID" width="120" />
        <el-table-column label="来源平台" width="100" align="center"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.platform_name }}</el-tag></template></el-table-column>
        <el-table-column prop="source_product_id" label="来源商品 ID" width="150" />
        <el-table-column label="上货微信小店类目" width="160"><template #default="{ row }">{{ row.wx_category || '-' }}</template></el-table-column>
        <el-table-column label="状态" width="90" align="center"><template #default="{ row }"><el-tag :type="itemType[row.status]" size="small">{{ row.status_name }}</el-tag></template></el-table-column>
        <el-table-column label="微信商品 ID" width="140"><template #default="{ row }">{{ row.wx_product_id || '-' }}</template></el-table-column>
        <el-table-column label="执行结果 / 失败原因" min-width="280" show-overflow-tooltip><template #default="{ row }">{{ row.message || '-' }}</template></el-table-column>
      </el-table>
      <div class="pager"><el-pagination v-model:current-page="itemPage" :page-size="50" :total="itemTotal" layout="total, prev, pager, next" @change="loadItems" /></div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http, download } from '../api'

const statusNames = { pending: '等待中', running: '执行中', success: '已完成', partial: '部分失败', failed: '执行失败' }
const statusType: Record<string, any> = { pending: 'info', running: 'primary', success: 'success', partial: 'warning', failed: 'danger' }
const itemStatusNames = { pending: '待执行', running: '执行中', success: '成功', failed: '失败', skipped: '已跳过' }
const itemType: Record<string, any> = { pending: 'info', running: 'primary', success: 'success', failed: 'warning', skipped: 'info' }

const q = reactive({ store_name: '', status: '', page: 1, size: 20 })
const items = ref<any[]>([])
const total = ref(0)
async function load() { const { data } = await http.get('/tasks', { params: q }); items.value = data.items; total.value = data.total }
function reset() { Object.assign(q, { store_name: '', status: '', page: 1 }); load() }
async function retry(row: any) { const { data } = await http.post(`/tasks/${row.id}/retry-failed`); ElMessage.success(`已重新排队 ${data.requeued} 个商品`); load() }

const detailVisible = ref(false)
const detailTask = ref<any>(null)
const detailItems = ref<any[]>([])
const itemTotal = ref(0)
const itemPage = ref(1)
const itemStatus = ref('')
async function loadItems() {
  if (!detailTask.value) return
  const { data } = await http.get(`/tasks/${detailTask.value.id}/items`, { params: { page: itemPage.value, size: 50, status: itemStatus.value } })
  detailItems.value = data.items; itemTotal.value = data.total; detailTask.value = data.task
}
function openDetail(row: any) { detailTask.value = row; itemPage.value = 1; itemStatus.value = ''; detailVisible.value = true; loadItems() }
const exportItems = () => download(`/tasks/${detailTask.value.id}/export`, {}, `${detailTask.value.name}.xlsx`)

let timer: number | undefined
onMounted(() => { load(); timer = window.setInterval(() => { load(); if (detailVisible.value) loadItems() }, 5000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.counts { display: flex; gap: 6px; margin-top: 6px; }
.prod { display: flex; gap: 8px; align-items: center; }
.two { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ml { margin-left: 8px; }
</style>

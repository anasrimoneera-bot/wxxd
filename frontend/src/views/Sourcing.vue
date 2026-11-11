<template>
  <div>
    <div class="card">
      <div class="card-head">
        <div class="card-title">找货源 <span class="muted">（1688 以图搜货，一次最多 10 张主图）</span></div>
        <el-select v-model="historyId" placeholder="历史记录" clearable style="width: 260px" @change="openJob">
          <el-option v-for="j in jobs" :key="j.id" :label="`#${j.id} · ${j.created_at} · ${j.total} 张`" :value="j.id" />
        </el-select>
      </div>
      <el-alert v-if="fromProducts.length" type="success" :closable="false" style="margin-bottom: 12px"
                :title="`已选择 ${fromProducts.length} 个商品的主图，找到货源后可直接「设为货源」`" />
      <el-input v-model="urls" type="textarea" :rows="4"
                placeholder="粘贴商品主图链接，每行一个（最多 10 个）。也可以在 商品云库 / 商品管理 勾选商品后点「找货源」。" />
      <div class="bar">
        <span class="muted">采购件数</span><el-input-number v-model="quantity" :min="1" size="small" />
        <span class="muted ml">1688 排序</span>
        <el-select v-model="sortType" size="small" style="width: 130px">
          <el-option label="综合" value="default" /><el-option label="价格从低到高" value="price_asc" />
          <el-option label="销量从高到低" value="sold_desc" /><el-option label="严选指数" value="yx_desc" />
        </el-select>
        <span class="muted ml">共 {{ imageCount }} 张</span>
        <el-button type="primary" icon="Search" :loading="starting" :disabled="!imageCount || imageCount > 10" @click="start">开始找货</el-button>
      </div>
      <p class="muted small">价格为供应商货价，<b>不含运费</b>；实际采购成本以 1688 下单页为准。</p>
    </div>

    <div v-if="job" class="card">
      <div class="card-head">
        <div class="card-title">
          找货结果 <span class="muted">#{{ job.id }}</span>
          <el-tag v-if="job.status !== 'done'" class="ml" type="primary">搜索中 {{ job.done }}/{{ job.total }}</el-tag>
        </div>
        <div>
          <el-radio-group v-model="order" size="small">
            <el-radio-button value="score">综合</el-radio-button><el-radio-button value="price">价格</el-radio-button>
            <el-radio-button value="sold">销量</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="mode" size="small" class="ml">
            <el-radio-button value="grid">大图模式</el-radio-button><el-radio-button value="list">列表模式</el-radio-button>
          </el-radio-group>
        </div>
      </div>

      <div v-for="q in job.queries" :key="q.id" class="row">
        <div class="src">
          <el-image :src="q.image_url" fit="cover" class="srcimg" :preview-src-list="[q.image_url]" preview-teleported />
          <div class="two small">{{ q.title || '粘贴的图片' }}</div>
          <el-tag v-if="q.status === 'pending'" size="small">排队中</el-tag>
          <el-tag v-else-if="q.status === 'failed'" size="small" type="danger">失败</el-tag>
          <el-tag v-else size="small" type="success">{{ q.offers.length }} 个货源</el-tag>
        </div>
        <div class="results">
          <el-alert v-if="q.error" :type="q.status === 'failed' ? 'error' : 'info'" :title="q.error" :closable="false" />
          <div v-if="mode === 'grid'" class="grid">
            <div v-for="o in sorted(q.offers)" :key="o.offer_id" class="offer" :class="{ best: o.offer_id === q.best_offer_id }">
              <div v-if="o.offer_id === q.best_offer_id" class="badge">最优</div>
              <el-image :src="o.image_url" fit="cover" class="oimg" lazy />
              <div class="otitle two" :title="o.title">{{ o.title }}</div>
              <div class="price">¥{{ o.price?.toFixed(2) ?? '-' }} <span class="muted small">货价 · 不含运费</span></div>
              <div class="meta"><span>起批 {{ o.moq ?? '-' }}{{ o.unit }}</span><span>已售 {{ o.sold }}</span></div>
              <div class="meta"><span>相似度 {{ pct(o, q) }}</span><span class="ellipsis" :title="o.supplier">{{ o.supplier }}</span></div>
              <div class="tags"><el-tag v-for="t in o.tags" :key="t" size="small" effect="plain">{{ t }}</el-tag></div>
              <div class="actions">
                <el-button v-if="q.linkable" size="small" type="primary" plain @click="link(q, o)">设为货源</el-button>
                <el-button size="small" @click="open(o.detail_url)">1688 打开</el-button>
              </div>
            </div>
          </div>
          <el-table v-else :data="sorted(q.offers)" size="small" border>
            <el-table-column label="图" width="70"><template #default="{ row }"><el-image :src="row.image_url" style="width: 48px; height: 48px" /></template></el-table-column>
            <el-table-column label="标题" min-width="220"><template #default="{ row }"><el-tag v-if="row.offer_id === q.best_offer_id" size="small" type="success">最优</el-tag> {{ row.title }}</template></el-table-column>
            <el-table-column label="货价" width="90" align="right"><template #default="{ row }">¥{{ row.price?.toFixed(2) ?? '-' }}</template></el-table-column>
            <el-table-column label="起批" width="80"><template #default="{ row }">{{ row.moq ?? '-' }}{{ row.unit }}</template></el-table-column>
            <el-table-column prop="sold" label="已售" width="90" />
            <el-table-column label="相似度" width="80"><template #default="{ row }">{{ pct(row, q) }}</template></el-table-column>
            <el-table-column prop="supplier" label="供应商" min-width="160" show-overflow-tooltip />
            <el-table-column label="操作" width="170">
              <template #default="{ row }">
                <el-button v-if="q.linkable" link type="primary" @click="link(q, row)">设为货源</el-button>
                <el-button link @click="open(row.detail_url)">1688 打开</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const route = useRoute()
const urls = ref('')
const quantity = ref(1)
const sortType = ref('default')
const starting = ref(false)
const job = ref<any>(null)
const jobs = ref<any[]>([])
const historyId = ref<number>()
const mode = ref('grid')
const order = ref('score')
const fromProducts = ref<{ kind: string; id: number }[]>([])

const pasted = computed(() => urls.value.split(/\s+/).map((u) => u.trim()).filter((u) => u.startsWith('http')))
const imageCount = computed(() => pasted.value.length + fromProducts.value.length)

function sorted(offers: any[]) {
  const a = [...offers]
  if (order.value === 'price') a.sort((x, y) => (x.price ?? 1e9) - (y.price ?? 1e9))
  else if (order.value === 'sold') a.sort((x, y) => y.sold - x.sold)
  else a.sort((x, y) => y.score - x.score)
  return a
}
function pct(o: any, q: any) {
  const top = Math.max(...q.offers.map((x: any) => x.score)) || 1
  const v = top > 1 ? o.score / 100 : o.score
  return `${Math.round(v * 100)}%`
}
const open = (u: string) => window.open(u, '_blank')

async function loadJobs() { jobs.value = (await http.get('/sourcing/jobs')).data }
async function openJob(id?: number) {
  if (!id) return
  job.value = (await http.get(`/sourcing/jobs/${id}`)).data
}
async function start() {
  starting.value = true
  try {
    const body: any = { image_urls: pasted.value, quantity: quantity.value, sort_type: sortType.value === 'default' ? '' : sortType.value,
      library_product_ids: fromProducts.value.filter((p) => p.kind === 'lib').map((p) => p.id),
      store_product_ids: fromProducts.value.filter((p) => p.kind === 'sp').map((p) => p.id) }
    const { data } = await http.post('/sourcing/jobs', body)
    await openJob(data.id); loadJobs()
  } finally { starting.value = false }
}
async function link(q: any, o: any) {
  await http.post('/sourcing/link', { query_id: q.id, offer_id: o.offer_id })
  ElMessage.success(`已设为货源：¥${o.price?.toFixed(2)}（${o.supplier}）`)
}

let timer: number | undefined
onMounted(() => {
  const lib = String(route.query.lib || '').split(',').filter(Boolean).map(Number)
  const sp = String(route.query.sp || '').split(',').filter(Boolean).map(Number)
  fromProducts.value = [...lib.map((id) => ({ kind: 'lib', id })), ...sp.map((id) => ({ kind: 'sp', id }))]
  loadJobs()
  timer = window.setInterval(() => { if (job.value && job.value.status !== 'done') openJob(job.value.id) }, 2000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.ml { margin-left: 10px; }
.small { font-size: 12px; }
.row { display: flex; gap: 16px; padding: 16px 0; border-top: 1px solid #eef0f4; }
.src { width: 170px; flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; }
.srcimg { width: 170px; height: 170px; border-radius: 8px; }
.results { flex: 1; min-width: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.offer { position: relative; border: 1px solid #eef0f4; border-radius: 10px; padding: 0 0 10px; overflow: hidden; background: #fff; }
.offer.best { border-color: #67c23a; box-shadow: 0 0 0 1px #67c23a; }
.badge { position: absolute; z-index: 2; top: 8px; left: 8px; background: #67c23a; color: #fff; font-size: 12px; padding: 2px 8px; border-radius: 10px; }
.oimg { width: 100%; aspect-ratio: 1; display: block; }
.otitle { margin: 8px 10px 4px; font-size: 13px; }
.price { margin: 0 10px; color: #f56c6c; font-size: 20px; font-weight: 700; }
.meta { display: flex; justify-content: space-between; gap: 8px; margin: 4px 10px 0; color: #6b7280; font-size: 12px; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 10px 0; }
.actions { display: flex; gap: 6px; margin: 8px 10px 0; }
.two { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>

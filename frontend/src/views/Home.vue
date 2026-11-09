<template>
  <div>
    <div class="card">
      <div class="card-title">概览</div>
      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="6" v-for="s in stats" :key="s.label">
          <div class="stat"><div class="muted">{{ s.label }}</div><div class="num">{{ s.value }}</div></div>
        </el-col>
      </el-row>
    </div>
    <div class="card">
      <div class="card-title">使用步骤</div>
      <el-steps :active="4" align-center style="margin-top: 20px">
        <el-step title="添加店铺" description="店铺管理：填入小店 AppID / AppSecret，并把服务器 IP 加入小店白名单" />
        <el-step title="上传数据包" description="商品云库：上传 .json / .txt 商品数据包" />
        <el-step title="配置发布" description="发布配置：新建上货模板，为店铺设置运费模板、退货地址、上货模板" />
        <el-step title="设置上货商品" description="选择分组，生成发布任务，在任务列表查看进度和失败原因" />
      </el-steps>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { http } from '../api'

const stats = ref([
  { label: '店铺', value: 0 }, { label: '云库商品', value: 0 }, { label: '上货模板', value: 0 }, { label: '执行中任务', value: 0 },
])

onMounted(async () => {
  const [s, l, t, k] = await Promise.all([
    http.get('/stores'), http.get('/library/stats'), http.get('/templates'), http.get('/tasks', { params: { status: 'running', size: 1 } }),
  ])
  stats.value[0].value = s.data.length
  stats.value[1].value = l.data.reduce((a: number, b: any) => a + b.count, 0)
  stats.value[2].value = t.data.length
  stats.value[3].value = k.data.total
})
</script>

<style scoped>
.stat { background: #f7f9fc; border-radius: 8px; padding: 16px; }
.num { font-size: 28px; font-weight: 700; margin-top: 6px; }
</style>

<template>
  <!-- 商品详情预览: the product as WeChat has it, laid out like its detail page on a phone -->
  <el-dialog v-model="visible" title="商品详情预览" width="440px" top="4vh" append-to-body class="pp-dialog">
    <div v-loading="loading" class="pp-phone">
      <div v-if="err" class="pp-err">{{ err }}</div>
      <template v-else-if="d">
        <el-carousel v-if="d.head_imgs.length" height="380px" indicator-position="inside" :autoplay="false" arrow="always">
          <el-carousel-item v-for="(u, i) in d.head_imgs" :key="i">
            <el-image :src="u" fit="contain" class="pp-head" referrerpolicy="no-referrer" :preview-src-list="d.head_imgs" :initial-index="i" preview-teleported />
          </el-carousel-item>
        </el-carousel>
        <div class="pp-block">
          <div class="pp-price">¥{{ yuan(d.price_min) }}<span v-if="d.price_max !== d.price_min"> - {{ yuan(d.price_max) }}</span></div>
          <div class="pp-title">{{ d.title }}</div>
          <div v-if="d.sub_title" class="pp-sub">{{ d.sub_title }}</div>
          <div class="pp-meta">{{ d.store_name }} · 库存 {{ d.stock }} · <el-tag size="small" :type="d.version === '线上版本' ? 'success' : 'warning'">{{ d.status_name }}</el-tag></div>
          <div v-if="d.version !== '线上版本'" class="pp-sub">显示的是{{ d.version }}</div>
        </div>
        <div v-if="d.skus.length" class="pp-block">
          <div class="pp-h">规格（{{ d.skus.length }}）</div>
          <div v-for="k in d.skus" :key="k.sku_id" class="pp-sku">
            <el-image v-if="k.thumb" :src="k.thumb" fit="cover" class="pp-sku-img" referrerpolicy="no-referrer" :preview-src-list="[k.thumb]" preview-teleported />
            <div class="pp-sku-name">{{ k.attrs || '默认' }}</div>
            <div class="pp-sku-price">¥{{ yuan(k.price) }}<div class="pp-sub">库存 {{ k.stock }}</div></div>
          </div>
        </div>
        <div v-if="d.attrs.length" class="pp-block">
          <div class="pp-h">商品参数</div>
          <div v-for="(a, i) in d.attrs" :key="i" class="pp-attr"><span>{{ a.key }}</span><span>{{ a.value }}</span></div>
        </div>
        <div class="pp-block">
          <div class="pp-h">商品详情</div>
          <div v-if="d.desc" class="pp-desc">{{ d.desc }}</div>
          <img v-for="(u, i) in d.desc_imgs" :key="i" :src="u" loading="lazy" referrerpolicy="no-referrer" class="pp-desc-img" />
          <div v-if="!d.desc && !d.desc_imgs.length" class="pp-sub">没有详情内容</div>
        </div>
      </template>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { http, yuan } from '../api'

const visible = ref(false)
const loading = ref(false)
const err = ref('')
const d = ref<any>(null)

async function open(storeId: number, wxProductId: string) {
  visible.value = true
  d.value = null; err.value = ''; loading.value = true
  try {
    d.value = (await http.get('/products/preview', { params: { store_id: storeId, wx_product_id: wxProductId }, timeout: 30000 })).data
  } catch (e: any) {
    err.value = e?.response?.data?.detail || '读取商品失败'
  } finally { loading.value = false }
}
defineExpose({ open })
</script>

<style scoped>
.pp-phone { min-height: 300px; max-height: 80vh; overflow-y: auto; background: #f5f5f5; margin: -10px -4px; border-radius: 8px; }
.pp-head { width: 100%; height: 380px; background: #fff; }
.pp-block { background: #fff; margin-bottom: 8px; padding: 12px 14px; }
.pp-price { color: #f53f3f; font-size: 22px; font-weight: 600; }
.pp-title { font-size: 15px; font-weight: 600; margin-top: 6px; line-height: 1.5; color: #1d2129; }
.pp-sub { color: #86909c; font-size: 12px; margin-top: 2px; }
.pp-meta { color: #86909c; font-size: 12px; margin-top: 8px; }
.pp-h { font-weight: 600; margin-bottom: 8px; color: #1d2129; }
.pp-sku { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid #f2f3f5; }
.pp-sku-img { width: 44px; height: 44px; border-radius: 4px; flex-shrink: 0; }
.pp-sku-name { flex: 1; font-size: 13px; }
.pp-sku-price { color: #f53f3f; text-align: right; font-size: 13px; }
.pp-attr { display: flex; gap: 12px; font-size: 13px; padding: 4px 0; }
.pp-attr span:first-child { color: #86909c; width: 80px; flex-shrink: 0; }
.pp-desc { white-space: pre-wrap; font-size: 13px; line-height: 1.7; margin-bottom: 8px; }
.pp-desc-img { display: block; width: 100%; }
.pp-err { padding: 40px 16px; text-align: center; color: #f53f3f; }
</style>

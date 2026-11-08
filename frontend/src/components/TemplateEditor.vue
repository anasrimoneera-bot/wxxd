<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)"
             :title="id ? '编辑上货模板' : '新增上货模板'" width="980px" top="4vh" destroy-on-close @open="init">
    <div v-if="cfg" class="editor">
      <div class="row"><span class="label req">模板名称</span><el-input v-model="name" style="width: 400px" maxlength="64" /></div>
      <div class="row"><span class="label">发货时效</span>
        <el-radio-group v-model="cfg.deliver_type"><el-radio-button value="spot">现货</el-radio-button><el-radio-button value="presale">全款预售</el-radio-button></el-radio-group>
        <span class="muted ml">（{{ cfg.deliver_hours }}小时）</span>
      </div>
      <div class="row"><span class="label">价格公式</span>
        <el-input-number v-model="cfg.price.threshold" :min="0" controls-position="right" class="num" />
        <span class="mx">元及以上时：促销价 ×</span>
        <el-input-number v-model="cfg.price.multiplier" :min="0" :step="0.1" :precision="2" controls-position="right" class="num" />
        <span class="mx">+</span>
        <el-input-number v-model="cfg.price.add" :min="0" :precision="2" controls-position="right" class="num" />
        <span class="mx">-</span>
        <el-input-number v-model="cfg.price.subtract" :min="0" :precision="2" controls-position="right" class="num" />
        <el-select v-model="cfg.price.rounding" style="width: 110px" class="ml">
          <el-option label="保留分" value="fen" /><el-option label="保留角" value="jiao" /><el-option label="保留元" value="yuan" />
        </el-select>
      </div>
      <div class="row"><span class="label">上架位置</span>
        <el-select v-model="cfg.listing" style="width: 400px">
          <el-option label="出售中（上架额度满后传入草稿箱）" value="on_sale" /><el-option label="草稿箱" value="draft" />
        </el-select>
      </div>
      <div class="row"><span class="label">标题设置</span>
        <span class="mx">标题前缀</span><el-input v-model="cfg.title_prefix" placeholder="请输入标题前缀" style="width: 180px" />
        <span class="mx">标题后缀</span><el-input v-model="cfg.title_suffix" placeholder="请输入标题后缀" style="width: 180px" />
        <span class="mx">最大长度</span><el-input-number v-model="cfg.title_max_len" :min="5" :max="60" controls-position="right" class="num" />
      </div>
      <div class="row top"><span class="label">关键词替换</span>
        <div>
          <div v-for="(r, i) in cfg.keyword_replacements" :key="i" class="kw">
            <el-input v-model="r.source" placeholder="原关键词" style="width: 200px" /><span class="mx">→</span>
            <el-input v-model="r.target" placeholder="替换词（为空表示删除）" style="width: 200px" />
            <el-button circle icon="Minus" class="ml" @click="cfg.keyword_replacements.splice(i, 1)" />
          </div>
          <el-button icon="Plus" size="small" @click="cfg.keyword_replacements.push({ source: '', target: '' })">添加替换词</el-button>
          <div class="danger">如果后面替换词为空就是删除该关键词</div>
          <div>标题违禁词（{{ cfg.title_banned_words.length }}）<el-button link type="primary" @click="editWords('title_banned_words', '标题违禁词')">编辑</el-button>
            <span class="ml">SKU违禁词（{{ cfg.sku_banned_words.length }}）</span><el-button link type="primary" @click="editWords('sku_banned_words', 'SKU违禁词')">编辑</el-button></div>
        </div>
      </div>

      <div class="sw" v-for="s in switches" :key="s.key"><el-switch v-model="cfg[s.key]" /><span class="sw-label">{{ s.label }}</span>
        <template v-if="s.key === 'filter_low_price_sku' && cfg.filter_low_price_sku">
          <span class="muted ml">低于中位价</span><el-input-number v-model="cfg.low_price_ratio" :min="0.05" :max="0.95" :step="0.05" :precision="2" size="small" class="ml" /><span class="muted">倍视为引流款</span>
        </template>
        <template v-if="s.key === 'fixed_tail' && cfg.fixed_tail">
          <span class="muted ml">尾数</span><el-input-number v-model="cfg.fixed_tail_value" :min="0" :max="0.99" :step="0.1" :precision="2" size="small" class="ml" />
        </template>
        <template v-if="s.key === 'ai_rewrite_title'"><span class="muted ml">（DeepSeek 改写，自动去重关键词）</span></template>
      </div>
      <div class="sw"><el-switch v-model="cfg.price_filter.enabled" /><span class="sw-label">上货过滤价格</span>
        <template v-if="cfg.price_filter.enabled"><span class="muted ml">只上传来源价在</span>
          <el-input-number v-model="cfg.price_filter.min" :min="0" size="small" class="ml" /><span class="mx">至</span>
          <el-input-number v-model="cfg.price_filter.max" :min="0" size="small" /><span class="muted ml">元之间的商品（0 表示不限）</span></template>
      </div>
      <div class="sw"><el-switch v-model="cfg.watermark.enabled" /><span class="sw-label">主图水印</span></div>
      <div v-if="cfg.watermark.enabled" class="wm">
        <span class="muted">水印样式</span>
        <el-select v-model="cfg.watermark.style" style="width: 150px" class="ml">
          <el-option v-for="w in watermarkStyles" :key="w.value" :label="w.label" :value="w.value" />
        </el-select>
        <span class="muted ml">透明度</span><el-input-number v-model="cfg.watermark.opacity" :min="5" :max="100" size="small" class="ml" /><span class="muted">%</span>
        <el-input v-model="cfg.watermark.text" maxlength="10" show-word-limit placeholder="自定义文字，留空使用店铺名" style="width: 260px" class="ml" />
      </div>
      <div class="sw" v-for="s in serviceSwitches" :key="s.key"><el-switch v-model="cfg[s.key]" /><span class="sw-label">{{ s.label }}</span></div>
      <div class="sw" v-for="d in deletes" :key="d.key"><span class="sw-label b">{{ d.label }}</span><el-switch v-model="cfg[d.key].enabled" class="ml" />
        <span class="muted ml">删除前</span><el-input-number v-model="cfg[d.key].front" :min="0" size="small" class="ml" />
        <span class="muted ml">张，删除后</span><el-input-number v-model="cfg[d.key].back" :min="0" size="small" class="ml" /><span class="muted ml">张</span>
      </div>
      <div class="sw"><el-switch v-model="cfg.stock_keep_original" /><span class="sw-label">库存设置：{{ cfg.stock_keep_original ? '按原有库存' : '固定库存' }}</span>
        <el-input-number v-if="!cfg.stock_keep_original" v-model="cfg.fixed_stock" :min="0" size="small" class="ml" />
      </div>
      <div class="sw warn"><el-switch v-model="cfg.auto_fill_required_attrs" />
        <div class="ml"><div class="danger b">自动补全必填属性</div><div class="danger small">数据包和属性推荐均未匹配时，从微信类目可选项中自动选择一个值</div></div>
      </div>
      <div class="attrs">
        <div><b>自定义商品属性</b><span class="muted ml">发布时优先使用，可补齐微信类目的必填属性。单位按需选择；不需要单位的属性直接留空。</span></div>
        <div v-for="(a, i) in cfg.custom_attrs" :key="i" class="kw">
          <el-input v-model="a.name" style="width: 200px" /><span class="mx">:</span>
          <el-input v-model="a.value" style="width: 200px" />
          <el-select v-model="a.unit" placeholder="无单位" clearable filterable allow-create style="width: 110px" class="ml">
            <el-option v-for="u in units" :key="u" :label="u" :value="u" />
          </el-select>
          <el-button circle icon="Plus" class="ml" @click="cfg.custom_attrs.splice(i + 1, 0, { name: '', value: '', unit: '' })" />
          <el-button circle icon="Minus" @click="cfg.custom_attrs.splice(i, 1)" />
        </div>
        <el-button v-if="!cfg.custom_attrs.length" icon="Plus" size="small" @click="cfg.custom_attrs.push({ name: '', value: '', unit: '' })">添加属性</el-button>
      </div>
      <div class="sw"><el-switch v-model="cfg.auto_apply_category" /><span class="sw-label">自动申请类目（店铺未开通该类目时，在保证金额度内尝试申请）</span>
        <template v-if="cfg.auto_apply_category"><span class="muted ml">保证金上限</span><el-input-number v-model="cfg.max_deposit" :min="0" :step="500" size="small" class="ml" /><span class="muted">元</span></template>
      </div>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { http } from '../api'

const props = defineProps<{ modelValue: boolean; id?: number | null }>()
const emit = defineEmits(['update:modelValue', 'saved'])
const cfg = ref<any>(null)
const name = ref('')
const saving = ref(false)

const switches = [
  { key: 'filter_low_price_sku', label: '规避低价引流SKU（过滤明显低于其他SKU价格的引流款）' },
  { key: 'freight_insurance', label: '开启运费险' },
  { key: 'seven_day_return', label: '支持七天无理由退货' },
  { key: 'filter_duplicates', label: '过滤重复商品（同一店铺已上传过的商品不再上传）' },
  { key: 'fixed_tail', label: '商品价格固定尾数' },
  { key: 'truncate_title', label: '标题超过最大限制时截断' },
  { key: 'ai_rewrite_title', label: 'AI 改写标题' },
  { key: 'auto_short_title', label: '自动生成短标题' },
  { key: 'upload_video', label: '是否上传商品视频' },
]
const serviceSwitches = [
  { key: 'damage_return', label: '支持坏损包退' },
  { key: 'fake_one_pay_three', label: '支持假一赔三' },
  { key: 'exchange', label: '支持换货' },
]
const deletes = [{ key: 'detail_img_delete', label: '详情图删除' }, { key: 'main_img_delete', label: '主图删除' }]
const watermarkStyles = [
  { value: 'simple_text', label: '简约文字' }, { value: 'half_label', label: '半透标签' }, { value: 'outline_badge', label: '描边徽标' },
  { value: 'tiled', label: '平铺暗纹' }, { value: 'center_light', label: '居中浅印' }, { value: 'vertical_corner', label: '竖排角标' },
]
const units = ['g', 'kg', 'ml', 'L', 'cm', 'mm', 'm', 'W', '个', '件', '只', '片', '双', '套', '%', '天', '月', '年']

async function init() {
  if (props.id) {
    const { data } = await http.get(`/templates/${props.id}`)
    name.value = data.name; cfg.value = data.config
  } else {
    name.value = ''; cfg.value = (await http.get('/templates/defaults')).data
  }
}

async function editWords(key: string, title: string) {
  const { value } = await ElMessageBox.prompt('每行一个词', title, {
    inputType: 'textarea', inputValue: cfg.value[key].join('\n'),
  })
  cfg.value[key] = (value || '').split(/\n|,|，/).map((s: string) => s.trim()).filter(Boolean)
}

async function save() {
  if (!name.value.trim()) return ElMessage.warning('请输入模板名称')
  const config = { ...cfg.value,
    keyword_replacements: cfg.value.keyword_replacements.filter((r: any) => r.source),
    custom_attrs: cfg.value.custom_attrs.filter((a: any) => a.name && a.value).map((a: any) => ({ ...a, unit: a.unit || '' })) }
  saving.value = true
  try {
    if (props.id) await http.put(`/templates/${props.id}`, { name: name.value, config })
    else await http.post('/templates', { name: name.value, config })
    ElMessage.success('已保存'); emit('saved'); emit('update:modelValue', false)
  } finally { saving.value = false }
}
</script>

<style scoped>
.editor { max-height: 74vh; overflow-y: auto; padding-right: 6px; }
.row, .sw, .attrs { border: 1px solid #eef0f4; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; display: flex; align-items: center; flex-wrap: wrap; gap: 4px 0; }
.row.top { align-items: flex-start; }
.attrs { display: block; }
.label { width: 90px; text-align: right; font-weight: 600; margin-right: 12px; }
.label.req::before { content: '* '; color: #f56c6c; }
.sw-label { margin-left: 10px; font-weight: 600; }
.ml { margin-left: 8px; }
.mx { margin: 0 8px; color: #6b7280; }
.num { width: 120px; }
.kw { display: flex; align-items: center; margin: 8px 0; }
.danger { color: #f56c6c; margin: 6px 0; }
.small { font-size: 12px; margin: 0; }
.b { font-weight: 600; }
.warn { background: #fef0f0; border-color: #fbc4c4; }
.wm { background: #ecf5ff; border: 1px solid #b3d8ff; border-radius: 8px; padding: 12px 16px; margin: -4px 0 10px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px 0; }
</style>

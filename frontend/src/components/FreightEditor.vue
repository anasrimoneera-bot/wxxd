<template>
  <el-dialog :model-value="modelValue" :title="tid ? '编辑运费模板' : '新建运费模板'" width="620px" append-to-body
             @update:model-value="(v: boolean) => emit('update:modelValue', v)" @open="init">
    <el-form v-loading="loading" label-width="100px">
      <el-alert type="info" :closable="false" style="margin-bottom: 10px"
                :title="`保存到微信小店「${storeName}」的运费模板（和小店后台 → 运费模板是同一个）`" />
      <el-form-item label="模板名称"><el-input v-model="f.name" maxlength="64" placeholder="例如：全国包邮（偏远地区不发）" /></el-form-item>
      <el-form-item label="发货地址">
        <el-select v-model="f.address_id" :placeholder="tid ? (f.address_text || '不修改') : '选择店铺的地址'" clearable style="width: 100%">
          <el-option v-for="a in addresses" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <div v-if="tid && f.address_text" class="muted small">当前：{{ f.address_text }}（不选就不修改）</div>
      </el-form-item>
      <el-form-item label="发货时间">
        <el-radio-group v-model="f.send_time">
          <el-radio v-for="t in opts.send_times" :key="t.id" :value="t.id">{{ t.name }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="计费方式">
        <el-radio-group v-model="f.shipping_method">
          <el-radio value="FREE">包邮</el-radio><el-radio value="CONDITION_FREE">条件包邮</el-radio><el-radio value="NO_FREE">不包邮</el-radio>
        </el-radio-group>
      </el-form-item>
      <template v-if="f.shipping_method !== 'FREE'">
        <el-form-item label="计费类型">
          <el-radio-group v-model="f.valuation_type"><el-radio value="PIECE">按件数</el-radio><el-radio value="WEIGHT">按重量</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="默认运费">
          <div class="row">
            <el-input-number v-model="f.first_val_amount" :min="1" :max="9999" controls-position="right" style="width: 100px" />
            <span>{{ unit }}内</span>
            <el-input-number v-model="f.first_price" :min="0" :max="999" :precision="2" controls-position="right" style="width: 110px" /><span>元，每增加</span>
            <el-input-number v-model="f.second_val_amount" :min="1" :max="9999" controls-position="right" style="width: 100px" />
            <span>{{ unit }}，增加</span>
            <el-input-number v-model="f.second_price" :min="0" :max="999" :precision="2" controls-position="right" style="width: 110px" /><span>元</span>
          </div>
          <div v-if="f.region_rules" class="muted small">另有 {{ f.region_rules }} 条「指定地区运费」（在小店后台设置的），保存时保留不变。</div>
        </el-form-item>
        <el-form-item v-if="f.shipping_method === 'CONDITION_FREE'" label="包邮条件">
          <div class="row">
            <el-radio-group v-model="f.condition"><el-radio value="piece">满件数</el-radio><el-radio value="amount">满金额</el-radio></el-radio-group>
            <template v-if="f.condition === 'piece'">满 <el-input-number v-model="f.min_piece" :min="1" :max="9999" controls-position="right" style="width: 100px" /> 件包邮</template>
            <template v-else>满 <el-input-number v-model="f.min_amount" :min="0.01" :max="99999" :precision="2" controls-position="right" style="width: 120px" /> 元包邮</template>
          </div>
          <div class="muted small">发货地区都适用；不满足时按上面的默认运费。</div>
        </el-form-item>
      </template>
      <el-form-item label="不发货地区">
        <el-select v-model="f.not_send_provinces" multiple filterable placeholder="不选 = 全国都发" style="width: 100%">
          <el-option v-for="p in opts.provinces" :key="p" :label="p" :value="p" />
        </el-select>
        <div class="row" style="margin-top: 4px">
          <el-button size="small" link type="primary" @click="f.not_send_provinces = [...new Set([...f.not_send_provinces, ...REMOTE])]">加上偏远地区（新疆、西藏、青海、内蒙古、宁夏、甘肃）</el-button>
        </div>
      </el-form-item>
      <el-form-item label="默认模板"><el-switch v-model="f.is_default" /><span class="muted small" style="margin-left: 8px">设为小店的默认运费模板</span></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存到小店</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '../api'

const props = defineProps<{ modelValue: boolean, storeId: number, storeName?: string, tid?: string, addresses: any[] }>()
const emit = defineEmits(['update:modelValue', 'saved'])
const REMOTE = ['新疆维吾尔自治区', '西藏自治区', '青海省', '内蒙古自治区', '宁夏回族自治区', '甘肃省']
const blank = () => ({ name: '', address_id: '', address_text: '', send_time: 'SendTime_FOUTYEIGHT_HOUR', shipping_method: 'FREE',
  valuation_type: 'PIECE', first_val_amount: 1, first_price: 0, second_val_amount: 1, second_price: 0, condition: 'piece',
  min_piece: 2, min_amount: 0, not_send_provinces: [] as string[], is_default: false, region_rules: 0 })
const f = reactive<any>(blank())
const opts = ref<any>({ provinces: [], send_times: [] })
const loading = ref(false)
const saving = ref(false)
const unit = computed(() => (f.valuation_type === 'WEIGHT' ? '千克' : '件'))
async function init() {
  Object.assign(f, blank())
  loading.value = true
  try {
    opts.value = (await http.get('/stores/freight-options')).data
    if (props.tid) Object.assign(f, (await http.get(`/stores/${props.storeId}/freight-templates/${props.tid}`)).data, { address_id: '' })
    else if (props.addresses.length === 1) f.address_id = props.addresses[0].id
  } finally { loading.value = false }
}
async function save() {
  if (!f.name.trim()) return ElMessage.warning('请填写模板名称')
  if (!props.tid && !f.address_id) return ElMessage.warning('请选择发货地址')
  saving.value = true
  try {
    const { data } = props.tid ? await http.put(`/stores/${props.storeId}/freight-templates/${props.tid}`, f)
      : await http.post(`/stores/${props.storeId}/freight-templates`, f)
    ElMessage.success('已保存到小店')
    emit('saved', data); emit('update:modelValue', false)
  } finally { saving.value = false }
}
</script>

<style scoped>
.row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.small { font-size: 12px; }
</style>

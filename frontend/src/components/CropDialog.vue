<template>
  <el-dialog :model-value="modelValue" title="框选主体" width="620px" append-to-body @update:model-value="emit('update:modelValue', $event)">
    <p class="muted small tip">在图片上按住鼠标拖动，框住要找的商品。1688 只会搜索框内的部分；选「整张图」则由 1688 自动识别主体。</p>
    <div class="stage" @mousedown.prevent="startDraw" @mousemove="moveDraw" @mouseup="endDraw" @mouseleave="endDraw">
      <img ref="img" :src="src" class="img" draggable="false" alt="" />
      <div v-if="box" class="box" :style="boxStyle" />
    </div>
    <template #footer>
      <el-button @click="box = null">整张图</el-button>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!!box && (box[2] < 0.05 || box[3] < 0.05)" @click="confirm">{{ confirmText }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ modelValue: boolean; src: string; crop?: number[] | null; confirmText?: string }>(),
  { crop: null, confirmText: '确定' })
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'confirm', crop: number[] | null): void }>()

const img = ref<HTMLImageElement>()
const box = ref<number[] | null>(null) // [x, y, w, h] as fractions of the image
let origin: [number, number] | null = null

watch(() => props.modelValue, (v) => { if (v) box.value = props.crop ? [...props.crop] : null })

const boxStyle = computed(() => {
  const [x, y, w, h] = box.value!
  return { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }
})

function point(e: MouseEvent): [number, number] {
  const r = img.value!.getBoundingClientRect()
  const clamp = (v: number) => Math.min(Math.max(v, 0), 1)
  return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)]
}
function startDraw(e: MouseEvent) { origin = point(e); box.value = [origin[0], origin[1], 0, 0] }
function moveDraw(e: MouseEvent) {
  if (!origin) return
  const [px, py] = point(e)
  box.value = [Math.min(origin[0], px), Math.min(origin[1], py), Math.abs(px - origin[0]), Math.abs(py - origin[1])]
}
function endDraw() {
  if (origin && box.value && box.value[2] < 0.02 && box.value[3] < 0.02) box.value = null // a click, not a drag
  origin = null
}
function confirm() {
  emit('confirm', box.value ? box.value.map((v) => Math.round(v * 10000) / 10000) : null)
  emit('update:modelValue', false)
}
</script>

<style scoped>
.tip { margin: 0 0 10px; }
.small { font-size: 12px; }
.stage { position: relative; display: inline-block; max-width: 100%; cursor: crosshair; user-select: none; line-height: 0; overflow: hidden; }
.img { max-width: 100%; max-height: 60vh; display: block; }
.box { position: absolute; border: 2px solid #409eff; box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.45); pointer-events: none; }
</style>

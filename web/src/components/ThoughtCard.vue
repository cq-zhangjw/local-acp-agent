<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { ChevronDownOutline, ChevronUpOutline, SparklesOutline } from '@vicons/ionicons5'

const props = defineProps<{ text: string; done?: boolean }>()
const { t } = useI18n()

const expanded = ref(false)
const startTime = Date.now()
const elapsed = ref(0)
const bodyRef = ref<HTMLElement | null>(null)
let timer: number | undefined

function tick() { elapsed.value = (Date.now() - startTime) / 1000 }
function stopTimer() { if (timer) { clearInterval(timer); timer = undefined } }

onMounted(() => {
  timer = window.setInterval(tick, 200)
})
onBeforeUnmount(stopTimer)

// When the thought finishes, freeze the elapsed time and collapse the card.
watch(() => props.done, (d) => {
  if (d) { tick(); stopTimer(); expanded.value = false }
})

// Auto-scroll to bottom while streaming (only while not done; after done the
// user may want to read from the top, so we don't force-scroll).
watch(() => props.text, async () => {
  if (props.done) return
  await nextTick()
  if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight
})

// 思考太短（<200 字符）不显示卡片——调用方在外面判断
const chars = computed(() => props.text.length)
</script>

<template>
  <div class="thought-card">
    <button class="thought-head" @click="expanded = !expanded">
      <NIcon :component="SparklesOutline" class="thought-spark" />
      <span v-if="!done" class="thought-title">
        {{ t('thinking') }}…
        <span class="thought-time">{{ elapsed.toFixed(1) }}s</span>
      </span>
      <span v-else class="thought-title">
        {{ t('thoughtDone') }} · {{ elapsed.toFixed(1) }}s · {{ chars }} chars
      </span>
      <NIcon :component="expanded ? ChevronUpOutline : ChevronDownOutline" class="thought-chevron" />
    </button>
    <div v-if="expanded || !done" class="thought-body">
      <div ref="bodyRef" class="thought-text">{{ text }}</div>
    </div>
  </div>
</template>

<style scoped>
.thought-card { margin:6px 0 10px; border:1px solid #dbe4ff; background:#f5f8ff; border-radius:10px; overflow:hidden; }
.thought-head { width:100%; display:flex; align-items:center; gap:8px; padding:7px 12px; background:transparent; border:0; cursor:pointer; font-size:12px; color:#4b5b9e; text-align:left; }
.thought-spark { font-size:14px; color:#6366f1; flex:none; }
.thought-title { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.thought-time { color:#818cf8; margin-left:6px; font-variant-numeric:tabular-nums; }
.thought-chevron { flex:none; font-size:14px; color:#818cf8; }
.thought-body { padding:0 12px 10px; }
.thought-text { font-family:ui-monospace,'Cascadia Code',Consolas,monospace; font-size:12px; line-height:1.6; color:#475569; white-space:pre-wrap; word-break:break-word; max-height:300px; overflow-y:auto; }
</style>

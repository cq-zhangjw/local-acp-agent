<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowForwardOutline, CloseOutline, FlashOutline } from '@vicons/ionicons5'
import { NButton, NIcon, NTag, NTooltip } from 'naive-ui'
import { useI18n } from 'vue-i18n'

interface QuickCommand {
  id: string
  title: string
  content: string
  agent: string
  model: string
  mode?: string
  autoSend: boolean
  role?: string
  webSearchOn?: boolean
  historyOn?: boolean
  jpMarkOn?: boolean
  showOnHome?: boolean
}

const emit = defineEmits<{
  (e: 'invoke', cmd: QuickCommand): void
}>()
const { t } = useI18n()

const list = ref<QuickCommand[]>([])

async function load() {
  const response = await fetch('/api/quick-commands')
  const data = await response.json()
  if (response.ok && data.ok) {
    list.value = (data.items || []).filter((command: QuickCommand) => command.showOnHome !== false)
  }
}

function invoke(cmd: QuickCommand) {
  emit('invoke', cmd)
}

async function removeFromHome(command: QuickCommand, event: Event) {
  event.stopPropagation()
  const response = await fetch(`/api/quick-commands/${encodeURIComponent(command.id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...command, showOnHome: false }),
  })
  if (response.ok) await load()
}

defineExpose({ reload: load })
onMounted(load)
</script>

<template>
  <div v-if="list.length" class="qc-bar">
    <div class="qc-heading">
      <NIcon :component="FlashOutline" />
      <span>{{ t('quickCommands.homeTitle') }}</span>
    </div>
    <div class="qc-scroll">
      <div v-for="cmd in list" :key="cmd.id" class="qc-card" role="button" tabindex="0" @click="invoke(cmd)" @keydown.enter.prevent="invoke(cmd)">
        <div class="qc-main">
          <strong class="qc-title">{{ cmd.title }}</strong>
          <span class="qc-preview">{{ cmd.content }}</span>
        </div>
        <div class="qc-meta">
          <NTag size="tiny" :bordered="false">{{ cmd.agent }}</NTag>
          <NTag v-if="cmd.model" size="tiny" :bordered="false" type="info">{{ cmd.model }}</NTag>
        </div>
        <NTooltip>
          <template #trigger>
            <NButton quaternary circle size="tiny" class="qc-remove" :aria-label="t('quickCommands.removeFromHome')" @click="removeFromHome(cmd, $event)">
              <template #icon><NIcon :component="CloseOutline" /></template>
            </NButton>
          </template>
          {{ t('quickCommands.removeFromHome') }}
        </NTooltip>
        <NIcon class="qc-run" :component="ArrowForwardOutline" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.qc-bar { margin:6px 0 10px; }
.qc-heading { display:flex; align-items:center; gap:5px; margin-bottom:6px; color:var(--ui-text-muted); font-size:11px; font-weight:600; }
.qc-scroll { display:flex; gap:8px; overflow-x:auto; padding:2px 1px 5px; }
.qc-scroll::-webkit-scrollbar { height:4px; }
.qc-scroll::-webkit-scrollbar-thumb { background:var(--ui-border-strong); border-radius:2px; }
.qc-card { position:relative; display:grid; min-width:250px; max-width:320px; flex:0 0 auto; grid-template-columns:minmax(0, 1fr) auto; align-items:center; gap:8px 12px; padding:9px 36px 9px 12px; border:1px solid var(--ui-border); border-radius:10px; background:var(--ui-surface); color:var(--ui-text); cursor:pointer; transition:border-color .15s ease, background .15s ease; }
.qc-card:hover,.qc-card:focus-visible { border-color:var(--ui-primary); background:var(--ui-primary-soft); outline:none; }
.qc-main { display:flex; min-width:0; flex-direction:column; gap:2px; }
.qc-title,.qc-preview { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.qc-title { font-size:12px; }
.qc-preview { color:var(--ui-text-muted); font-size:11px; }
.qc-meta { display:flex; gap:4px; }
.qc-remove { position:absolute; top:3px; right:3px; opacity:0; }
.qc-card:hover .qc-remove,.qc-card:focus-within .qc-remove { opacity:1; }
.qc-run { position:absolute; right:11px; bottom:10px; color:var(--ui-primary); font-size:14px; }
@media (hover:none) { .qc-remove { opacity:1; } }
</style>

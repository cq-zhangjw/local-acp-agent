<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NModal, NButton, NInput, NSelect, NSwitch, NTag } from 'naive-ui'

interface QuickCommand {
  id: string
  title: string
  content: string
  agent: string
  model: string
  mode: string
  autoSend: boolean
  type: string
  createdAt: number
}

const props = defineProps<{
  agents: any[]
  models: any[]
  currentAgent: string
  currentModel: string
}>()
const emit = defineEmits<{
  (e: 'invoke', cmd: QuickCommand): void
}>()

const list = ref<QuickCommand[]>([])
const showModal = ref(false)
const editing = ref<QuickCommand | null>(null)
const form = ref({ title: '', content: '', agent: 'default', model: '', mode: 'task', autoSend: false, type: 'prompt' })

// Task mode: can't pick the lightweight Chat agent. Chat mode: agent is Chat, model locked.
const agentOptions = computed(() => {
  const opts = props.agents.map(a => ({ label: a.name, value: a.name }))
  if (form.value.mode === 'task') return opts.filter(o => o.value !== 'Chat')
  return opts
})
const modelOptions = computed(() => [{ label: '默认', value: '' }, ...props.models.map(m => ({ label: m.model_name, value: m.model_name }))])

function onModeChange(mode: string) {
  if (mode === 'chat') {
    form.value.agent = 'Chat'
    form.value.model = ''
  } else if (form.value.agent === 'Chat') {
    form.value.agent = 'default'
  }
}

async function load() {
  try {
    const r = await fetch('/api/quick-commands')
    const d = await r.json()
    if (d.ok) list.value = d.items
  } catch {}
}

function openNew() {
  editing.value = null
  form.value = { title: '', content: '', agent: props.currentAgent, model: props.currentModel, mode: 'task', autoSend: false, type: 'prompt' }
  showModal.value = true
}

function openNewWithContent(text: string) {
  editing.value = null
  form.value = {
    title: text.slice(0, 20),
    content: text,
    agent: props.currentAgent === 'Chat' ? 'Chat' : 'default',
    model: '',
    mode: props.currentAgent === 'Chat' ? 'chat' : 'task',
    autoSend: false,
    type: 'prompt',
  }
  showModal.value = true
}

defineExpose({ openNew, openNewWithContent })

function openEdit(cmd: QuickCommand) {
  editing.value = cmd
  form.value = { title: cmd.title, content: cmd.content, agent: cmd.agent, model: cmd.model, mode: cmd.mode, autoSend: cmd.autoSend, type: cmd.type || 'prompt' }
  showModal.value = true
}

async function save() {
  if (!form.value.title.trim()) return
  const body = JSON.stringify(form.value)
  if (editing.value) {
    await fetch(`/api/quick-commands/${editing.value.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body })
  } else {
    await fetch('/api/quick-commands', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body })
  }
  showModal.value = false
  await load()
}

async function remove(cmd: QuickCommand, ev: Event) {
  ev.stopPropagation()
  if (!confirm(`删除快捷指令 "${cmd.title}"？`)) return
  await fetch(`/api/quick-commands/${cmd.id}`, { method: 'DELETE' })
  await load()
}

function invoke(cmd: QuickCommand) {
  emit('invoke', cmd)
}

onMounted(load)
</script>

<template>
  <div class="qc-bar">
    <div class="qc-scroll">
      <div class="qc-card qc-new" @click="openNew">
        <span class="qc-new-plus">＋</span>
        <span class="qc-new-label">新建快捷指令</span>
      </div>
      <div v-for="cmd in list" :key="cmd.id" class="qc-card" @click="invoke(cmd)">
        <div class="qc-head">
          <span class="qc-title">{{ cmd.title }}</span>
          <span class="qc-actions" @click.stop>
            <button class="qc-btn" @click="openEdit(cmd)">✎</button>
            <button class="qc-btn qc-btn-del" @click="remove(cmd, $event)">✕</button>
          </span>
        </div>
        <div class="qc-preview">{{ cmd.content }}</div>
        <div class="qc-tags">
          <NTag size="tiny" :bordered="false">{{ cmd.agent }}</NTag>
          <NTag v-if="cmd.model" size="tiny" :bordered="false" type="info">{{ cmd.model }}</NTag>
          <NTag size="tiny" :bordered="false" :type="cmd.mode === 'chat' ? 'success' : 'warning'">{{ cmd.mode === 'chat' ? 'Chat' : 'Task' }}</NTag>
        </div>
      </div>
    </div>

    <NModal v-model:show="showModal" preset="card" style="width:520px" :title="editing ? '编辑快捷指令' : '新建快捷指令'">
      <div class="qc-form">
        <label>类型</label>
        <NSelect v-model:value="form.type" :options="[{ label: '发送为消息（填入输入框）', value: 'prompt' }, { label: '设为系统提示词（角色/行为规范）', value: 'system' }]" />
        <label>模式</label>
        <NSelect v-model:value="form.mode" :options="[{ label: 'Chat（轻量对话，锁默认模型）', value: 'chat' }, { label: 'Task（完整 Agent 工作区）', value: 'task' }]" @update:value="onModeChange" />
        <label>标题</label>
        <NInput v-model:value="form.title" placeholder="如：分析项目结构" maxlength="30" show-count />
        <label>指令内容</label>
        <NInput v-model:value="form.content" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" placeholder="输入框中会填入这段内容" />
        <div class="qc-row">
          <div class="qc-col">
            <label>Agent</label>
            <NSelect v-model:value="form.agent" :options="agentOptions" />
          </div>
          <div class="qc-col">
            <label>模型{{ form.mode === 'chat' ? '（Chat 模式锁默认）' : '' }}</label>
            <NSelect v-model:value="form.model" :options="modelOptions" :disabled="form.mode === 'chat'" />
          </div>
        </div>
        <div class="qc-row">
          <div class="qc-col qc-col-switch">
            <label>自动发送</label>
            <NSwitch v-model:value="form.autoSend" size="small" />
          </div>
        </div>
        <div class="qc-actions-right">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" @click="save">保存</NButton>
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.qc-bar { margin: 4px 0 8px; }
.qc-scroll { display:flex; gap:10px; overflow-x:auto; padding-bottom:4px; }
.qc-scroll::-webkit-scrollbar { height:4px; }
.qc-scroll::-webkit-scrollbar-thumb { background:#cbd5e1; border-radius:2px; }
.qc-card { flex:0 0 220px; padding:10px 12px; border:1px solid #e2e8f0; border-radius:10px; background:#fff; cursor:pointer; transition:border-color .15s, box-shadow .15s; }
.qc-card:hover { border-color:#94a3b8; box-shadow:0 2px 8px rgba(0,0,0,.06); }
.qc-new { border:1.5px dashed #cbd5e1; display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:90px; color:#94a3b8; }
.qc-new-plus { font-size:22px; line-height:1; }
.qc-new-label { font-size:12px; margin-top:4px; }
.qc-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:4px; }
.qc-title { font-size:13px; font-weight:600; color:#1e293b; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px; }
.qc-actions { display:none; gap:2px; }
.qc-card:hover .qc-actions { display:flex; }
.qc-btn { border:0; background:transparent; cursor:pointer; font-size:11px; color:#64748b; padding:0 2px; }
.qc-btn-del:hover { color:#ef4444; }
.qc-preview { font-size:12px; color:#64748b; line-height:1.5; height:36px; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; margin-bottom:6px; }
.qc-tags { display:flex; gap:4px; flex-wrap:wrap; }
.qc-form label { display:block; font-size:12px; color:#64748b; margin:10px 0 4px; }
.qc-row { display:flex; gap:12px; }
.qc-col { flex:1; }
.qc-col-switch { display:flex; align-items:center; gap:8px; }
.qc-col-switch label { margin:0; flex:1; }
.qc-actions-right { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
</style>

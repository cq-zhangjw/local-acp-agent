<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDownOutline } from '@vicons/ionicons5'
import { NCollapse, NCollapseItem, NIcon, NTag } from 'naive-ui'
import { useI18n } from 'vue-i18n'

interface PlanEntry { title: string; status: string }
interface ToolCallEntry { id: string; name: string; title?: string; status: string; rawInput?: unknown; output?: unknown }
interface TodoItem {
  index: number
  title: string
  description?: string | null
  status: 'pending' | 'in_progress' | 'completed' | 'blocked'
  note?: string | null
  updatedAt?: number
}

const props = defineProps<{
  process: { startedAt: number; completedAt?: number; plan: PlanEntry[]; toolCalls: ToolCallEntry[] }
  todoItems?: TodoItem[] | null
}>()

const { t } = useI18n()
const stepCount = computed(() => props.process.plan.length + props.process.toolCalls.length)
const duration = computed(() => Math.max(0, Math.round(((props.process.completedAt ?? Date.now()) - props.process.startedAt) / 1000)))

const todoDone = computed(() => (props.todoItems ?? []).filter((item) => item.status === 'completed').length)

function statusText(status: string) {
  return t(`status.${status}`)
}

function todoIcon(status: string) {
  switch (status) {
    case 'completed': return '✓'
    case 'in_progress': return '●'
    case 'blocked': return '⚠'
    default: return '○'
  }
}
</script>

<template>
  <NCollapse class="process-collapse">
    <NCollapseItem name="process">
      <template #header>
        <div class="process-heading">
          <NIcon :component="ChevronDownOutline" />
          <span>{{ t('viewProcess') }}</span>
          <small>{{ stepCount }} {{ t('steps') }} · {{ duration }} {{ t('seconds') }}</small>
        </div>
      </template>

      <!-- 任务分步清单（todo） -->
      <div v-if="todoItems && todoItems.length" class="todo-panel">
        <div class="todo-heading">
          <span>{{ t('todoPanel') }}</span>
          <small>{{ todoDone }}/{{ todoItems.length }}</small>
        </div>
        <div class="todo-list">
          <div v-for="item in todoItems" :key="`${item.index}-${item.title}`" class="todo-item" :class="`todo-item--${item.status}`">
            <span class="todo-check">{{ todoIcon(item.status) }}</span>
            <span class="todo-title">{{ item.title }}</span>
            <span v-if="item.note" class="todo-note">{{ item.note }}</span>
          </div>
        </div>
      </div>

      <div v-if="process.plan.length" class="plan-list">
        <div v-for="(step, index) in process.plan" :key="`${step.title}-${index}`" class="plan-item">
          <span class="plan-index">{{ index + 1 }}</span>
          <span>{{ step.title }}</span>
          <NTag size="small" :type="step.status === 'completed' ? 'success' : 'info'">{{ statusText(step.status) }}</NTag>
        </div>
      </div>
    </NCollapseItem>
  </NCollapse>
</template>

<style scoped>
.process-collapse { margin-top:14px; background:var(--ui-surface); border:1px solid var(--ui-border); border-radius:10px; overflow:hidden; }
.process-collapse :deep(.n-collapse-item__header) { padding:12px 16px; }
.process-collapse :deep(.n-collapse-item:first-child > .n-collapse-item__header) { padding-top:12px; }
.process-collapse :deep(.n-collapse-item__content-inner) { padding:0 16px 12px; }
.process-heading { display:flex; gap:8px; align-items:center; min-width:0; color:var(--ui-text-secondary); font-size:13px; }
.process-heading small { color:var(--ui-text-muted); font-size:11px; }
.todo-panel { padding:8px 0 10px; border-bottom:1px dashed var(--ui-border); margin-bottom:8px; }
.todo-heading { display:flex; gap:8px; align-items:center; color:var(--ui-text-secondary); font-size:13px; font-weight:600; }
.todo-heading small { color:var(--ui-text-muted); font-size:11px; font-weight:400; margin-left:auto; }
.todo-list { display:flex; flex-direction:column; gap:6px; padding:8px 0 0; }
.todo-item { display:flex; gap:8px; align-items:flex-start; color:var(--ui-text-secondary); font-size:13px; line-height:1.5; }
.todo-check { flex:0 0 auto; width:16px; text-align:center; font-size:13px; }
.todo-title { min-width:0; overflow-wrap:anywhere; }
.todo-note { flex:0 0 auto; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ui-text-muted); font-size:11px; background:var(--ui-surface-muted); border-radius:4px; padding:0 5px; }
.todo-item--completed .todo-check { color:var(--ui-success); }
.todo-item--completed .todo-title { color:var(--ui-text-muted); text-decoration:line-through; }
.todo-item--in_progress .todo-check { color:var(--ui-primary); }
.todo-item--in_progress .todo-title { color:var(--ui-text); font-weight:600; }
.todo-item--blocked .todo-check { color:var(--ui-warning); }
.todo-item--blocked .todo-title { color:var(--ui-warning); }
.plan-list { display:flex; flex-direction:column; gap:8px; padding:6px 0 8px; }
.plan-item { display:flex; gap:8px; align-items:center; color:var(--ui-text-secondary); font-size:13px; }
.plan-index { display:grid; place-items:center; width:19px; height:19px; color:var(--ui-primary); background:var(--ui-primary-soft); border-radius:50%; font-size:11px; }
.plan-item :deep(.n-tag) { margin-left:auto; }
</style>

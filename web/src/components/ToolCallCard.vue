<script setup lang="ts">
import { computed } from 'vue'
import { NCollapse, NCollapseItem } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import MarkdownMessage from './MarkdownMessage.vue'

type ToolStatus = 'running' | 'completed' | 'failed' | 'cancelled' | 'waiting_permission'

interface ToolCallEntry {
  id: string
  name: string
  title?: string
  status: ToolStatus
  rawInput?: unknown
  output?: unknown
}

const props = defineProps<{ tool: ToolCallEntry }>()
const emit = defineEmits<{ (e: 'open-file', path: string): void }>()
const { t } = useI18n()

// 内置工具名：title 与之相同则视为"只有工具名、无正文"
const BUILTIN_TOOL_NAMES = new Set(['read_file', 'write_file', 'edit_file', 'glob', 'grep', 'ls', 'delete', 'task', 'execute'])

// 徽标与正文统一在渲染层推断（源数据是 title）：
// - "Read/Write/Edit `path`" → 徽标 Read/Write/Edit，正文为 path
// - "Execute: cmd" 或整条命令（execute）→ 徽标 Run，正文为命令
// - title 为内置工具名（glob/grep/ls...）→ 徽标该名，正文空
// - 推断不出 → 用 name 兜底（历史数据 name 可能是整条 title，此时徽标 Run）
// 这样新会话与历史会话（旧 name 格式）都能正确渲染
const display = computed(() => {
  const title = String(props.tool.title ?? '').trim()
  const name = String(props.tool.name ?? '').trim()
  const m = title.match(/^(Read|Write|Edit|Execute)\s*[:\s]\s*(.*)$/s)
  let badge = ''
  if (m) badge = m[1] === 'Execute' ? 'Run' : m[1]
  else if (BUILTIN_TOOL_NAMES.has(title) && title !== 'execute') badge = title
  // name 兜底：与 title 相同（历史 execute 把整条命令存进 name）或本身就是 execute → 视为无名，占位 Run
  if (!badge) badge = (name && name !== title && name !== 'execute') ? name : 'Run'
  // 正文：去掉工具名前缀；若剩余仍是工具名则留空（避免 "glob glob"）
  let body = m ? m[2].trim() : title
  if (body && (body === badge || BUILTIN_TOOL_NAMES.has(body))) body = ''
  return { badge, body }
})

function jsonValue(value: unknown) {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value ?? {}, null, 2) } catch { return String(value ?? '') }
}

// 参数理论上始终是 JSON 对象/字符串 → 用 ```json 代码块 + markdown 渲染（带语法高亮与复制按钮）
const paramsMarkdown = computed(() => `\`\`\`json\n${jsonValue(props.tool.rawInput)}\n\`\`\``)

// 返回值：统一用 ```bash 代码块包裹（read_file/execute/工具返回都是命令行风格输出）；
// 若为对象则回退为 JSON 代码块
const outputMarkdown = computed(() => {
  const raw = props.tool.output
  if (typeof raw === 'string') return `\`\`\`bash\n${raw}\n\`\`\``
  return `\`\`\`json\n${jsonValue(raw)}\n\`\`\``
})

// MCP / 内置工具把异常当正常 ToolMessage 文本返回，后端事件里 status 仍是 completed。
// 这里从返回值文本推断失败，徽标才能正确标红。
// 不锚定开头、不依赖语言：包含这些错误特征即视为失败（容错 output 前面的空白/BOM）。
const FAIL_PATTERNS: RegExp[] = [
  /工具调用失败/,
  /MCPError\s*[:：]/,
  /Traceback \(most recent call last\)/,
  /ModuleNotFoundError\s*:/,
  /Connection closed/i,
]
const effectiveStatus = computed<ToolStatus>(() => {
  if (props.tool.status === 'failed') return 'failed'
  const out = typeof props.tool.output === 'string' ? props.tool.output : ''
  if (!out) return props.tool.status
  if (FAIL_PATTERNS.some((re) => re.test(out))) return 'failed'
  return props.tool.status
})

// 状态标签用自绘 span（不用 NTag：inline-flex + flex:auto 在折叠头 flex 容器里易参与挤压）
function statusClass(status: ToolStatus) {
  if (status === 'completed') return 'tool-status--success'
  if (status === 'failed') return 'tool-status--error'
  if (status === 'waiting_permission') return 'tool-status--warning'
  return 'tool-status--info'
}

function statusText(status: string) {
  return t(`status.${status}`)
}
</script>

<template>
  <NCollapse class="tool-call-collapse">
    <NCollapseItem name="tool">
      <template #header>
        <div class="tool-heading">
          <code class="tool-badge">{{ display.badge }}</code>
          <span v-if="display.body" class="tool-name" :title="tool.title || display.body">{{ display.body }}</span>
          <span class="tool-status" :class="statusClass(effectiveStatus)">{{ statusText(effectiveStatus) }}</span>
        </div>
      </template>

      <!-- 合并为一个"调用详细"区域：先参数后返回值，不再切换 tab -->
      <div class="tool-detail">
        <div class="tool-detail__section">
          <div class="tool-detail__label">{{ t('parameters') }}</div>
          <MarkdownMessage :content="paramsMarkdown" :linkify-paths="false" @open-file="(p) => emit('open-file', p)" />
        </div>

        <div class="tool-detail__section">
          <div class="tool-detail__label">{{ t('output') }}</div>
          <MarkdownMessage :content="outputMarkdown" :linkify-paths="false" @open-file="(p) => emit('open-file', p)" />
        </div>
      </div>
    </NCollapseItem>
  </NCollapse>
</template>

<style scoped>
.tool-call-collapse { margin-top:10px; background:var(--ui-surface); border:1px solid var(--ui-border); border-radius:10px; overflow:hidden; }
.tool-call-collapse :deep(.n-collapse-item__header) { padding:12px 16px; }
.tool-call-collapse :deep(.n-collapse-item:first-child > .n-collapse-item__header) { padding-top:12px; }
.tool-call-collapse :deep(.n-collapse-item__content-inner) { padding:0 16px 12px; }
.tool-heading { flex:1; min-width:0; width:100%; display:flex; gap:8px; align-items:center; }
.tool-badge { flex:none; max-width:45%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font:12px/1.5 "Cascadia Code",Consolas,monospace; color:var(--ui-primary); background:var(--ui-primary-soft); border:1px solid var(--ui-border-strong); border-radius:6px; padding:0 8px; }
.tool-name { flex:1; min-width:0; max-width:450px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; color:var(--text); }
.tool-status { flex:none; display:inline-block; margin-left:auto; padding:1px 10px; border-radius:6px; font-size:12px; line-height:20px; white-space:nowrap; }
.tool-status--success { color:var(--ui-success); background:color-mix(in srgb, var(--ui-success) 14%, transparent); }
.tool-status--error { color:var(--ui-error); background:color-mix(in srgb, var(--ui-error) 14%, transparent); }
.tool-status--warning { color:var(--ui-warning); background:color-mix(in srgb, var(--ui-warning) 14%, transparent); }
.tool-status--info { color:var(--ui-primary); background:var(--ui-primary-soft); }
.tool-detail { padding:0; }
.tool-detail__section { padding:2px 0 4px; }
.tool-detail__label { font-size:12px; color:var(--ui-text-muted); margin:0 0 2px; }
.tool-detail__section :deep(.markdown-message) { font-size:13px; }
.tool-detail__section :deep(pre), .tool-detail__section :deep(code) { white-space:pre; }
.tool-detail__section + .tool-detail__section { border-top:1px dashed var(--ui-border); margin-top:8px; padding-top:12px; }
.tool-detail__section:last-child { max-height:340px; overflow-y:auto; }
</style>

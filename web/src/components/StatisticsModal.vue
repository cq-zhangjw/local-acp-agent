<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { ECharts } from 'echarts/core'
import {
  NAlert,
  NButton,
  NDataTable,
  NDatePicker,
  NInput,
  NModal,
  NPagination,
  NSelect,
  NSpin,
  NTag,
  type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useTheme } from '../theme'

echarts.use([
  LineChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

interface StatisticsRecord {
  id: string
  started_at: string
  machine_name: string
  user_name?: string | null
  mode: 'chat' | 'task'
  agent_name: string
  role_name?: string | null
  model_name?: string | null
  tool_call_count: number
  ai_call_count: number
  duration_ms?: number | null
  question: string
  status: string
}

interface Summary {
  requestCount: number
  completedCount: number
  failedCount: number
  cancelledCount: number
  chatCount: number
  taskCount: number
  toolCallCount: number
  aiCallCount: number
  averageDurationMs: number
  p95DurationMs: number
  failureRate: number
  trend: Array<{
    day: string
    requestCount: number
    aiCallCount: number
    toolCallCount: number
  }>
}

interface Options {
  machineNames: string[]
  userNames: string[]
  agentNames: string[]
  roleNames: string[]
  modelNames: string[]
  statuses: string[]
}

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()
const { t, locale } = useI18n()
const { resolved } = useTheme()

const loading = ref(false)
const errorText = ref('')
const dateRange = ref<[number, number] | null>(null)
const machineName = ref<string | null>(null)
const userName = ref<string | null>(null)
const agentName = ref<string | null>(null)
const roleName = ref<string | null>(null)
const modelName = ref<string | null>(null)
const status = ref<string | null>(null)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const records = ref<StatisticsRecord[]>([])
const filterOptions = ref<Options>({
  machineNames: [],
  userNames: [],
  agentNames: [],
  roleNames: [],
  modelNames: [],
  statuses: [],
})
const summary = ref<Summary>({
  requestCount: 0,
  completedCount: 0,
  failedCount: 0,
  cancelledCount: 0,
  chatCount: 0,
  taskCount: 0,
  toolCallCount: 0,
  aiCallCount: 0,
  averageDurationMs: 0,
  p95DurationMs: 0,
  failureRate: 0,
  trend: [],
})
const chartElement = ref<HTMLElement | null>(null)
let chart: ECharts | null = null

const machineOptions = computed(() =>
  filterOptions.value.machineNames.map((value) => ({ label: value, value }))
)
const userOptions = computed(() =>
  filterOptions.value.userNames.map((value) => ({ label: value, value }))
)
const agentOptions = computed(() =>
  filterOptions.value.agentNames.map((value) => ({ label: value, value }))
)
const roleOptions = computed(() =>
  filterOptions.value.roleNames.map((value) => ({ label: value, value }))
)
const modelOptions = computed(() =>
  filterOptions.value.modelNames.map((value) => ({ label: value, value }))
)
const statusOptions = computed(() =>
  filterOptions.value.statuses.map((value) => ({
    label: statusLabel(value),
    value,
  }))
)

const metrics = computed(() => [
  { label: t('statistics.requestCount'), value: summary.value.requestCount },
  { label: t('statistics.toolCallCount'), value: summary.value.toolCallCount },
  { label: t('statistics.aiCallCount'), value: summary.value.aiCallCount },
  { label: t('statistics.averageDuration'), value: formatDuration(summary.value.averageDurationMs) },
  { label: t('statistics.p95Duration'), value: formatDuration(summary.value.p95DurationMs) },
  { label: t('statistics.failureRate'), value: `${(summary.value.failureRate * 100).toFixed(1)}%` },
])

function statusLabel(value: string) {
  const key = `statistics.statuses.${value}`
  const translated = t(key)
  return translated === key ? value : translated
}

function statusType(value: string) {
  if (value === 'completed') return 'success'
  if (value === 'failed' || value === 'interrupted') return 'error'
  if (value === 'cancelled') return 'warning'
  return 'info'
}

function formatDuration(value?: number | null) {
  if (!value) return '0 ms'
  if (value < 1000) return `${value} ms`
  if (value < 60000) return `${(value / 1000).toFixed(1)} s`
  return `${(value / 60000).toFixed(1)} min`
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(new Date(value))
}

const columns = computed<DataTableColumns<StatisticsRecord>>(() => [
  { title: t('statistics.time'), key: 'started_at', width: 168, render: (row) => formatTime(row.started_at) },
  { title: t('statistics.machineName'), key: 'machine_name', width: 130, ellipsis: { tooltip: true } },
  { title: t('statistics.userName'), key: 'user_name', width: 150, ellipsis: { tooltip: true }, render: (row) => row.user_name || '-' },
  { title: t('statistics.agentName'), key: 'agent_name', width: 120, ellipsis: { tooltip: true } },
  { title: t('statistics.roleName'), key: 'role_name', width: 120, ellipsis: { tooltip: true }, render: (row) => row.role_name || '-' },
  { title: t('statistics.modelName'), key: 'model_name', width: 130, ellipsis: { tooltip: true }, render: (row) => row.model_name || '-' },
  { title: t('statistics.toolCalls'), key: 'tool_call_count', width: 92, align: 'right' },
  { title: t('statistics.aiCalls'), key: 'ai_call_count', width: 82, align: 'right' },
  { title: t('statistics.duration'), key: 'duration_ms', width: 100, align: 'right', render: (row) => formatDuration(row.duration_ms) },
  {
    title: t('statistics.status'),
    key: 'status',
    width: 100,
    render: (row) => h(
      NTag,
      { size: 'small', bordered: false, type: statusType(row.status) },
      { default: () => statusLabel(row.status) }
    ),
  },
  {
    title: t('statistics.question'),
    key: 'question',
    minWidth: 260,
    ellipsis: { tooltip: true },
    render: (row) => row.question || t('statistics.questionHidden'),
  },
])

function buildParams(includePage = false) {
  const params = new URLSearchParams()
  if (dateRange.value) {
    params.set('from', new Date(dateRange.value[0]).toISOString())
    params.set('to', new Date(dateRange.value[1]).toISOString())
  }
  if (machineName.value) params.set('machineName', machineName.value)
  if (userName.value) params.set('userName', userName.value)
  if (agentName.value) params.set('agentName', agentName.value)
  if (roleName.value) params.set('roleName', roleName.value)
  if (modelName.value) params.set('modelName', modelName.value)
  if (status.value) params.set('status', status.value)
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (includePage) {
    params.set('page', String(page.value))
    params.set('pageSize', String(pageSize.value))
  }
  return params
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function loadOptions() {
  filterOptions.value = await fetchJson<Options>('/api/statistics/options')
}

async function loadData() {
  loading.value = true
  errorText.value = ''
  try {
    const filterQuery = buildParams()
    const recordQuery = buildParams(true)
    const [summaryData, recordData] = await Promise.all([
      fetchJson<Summary>(`/api/statistics/summary?${filterQuery}`),
      fetchJson<{ items: StatisticsRecord[]; total: number }>(`/api/statistics/records?${recordQuery}`),
    ])
    summary.value = summaryData
    records.value = recordData.items
    total.value = recordData.total
    await nextTick()
    renderChart()
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
  }
}

async function initialize() {
  loading.value = true
  errorText.value = ''
  try {
    await loadOptions()
    await loadData()
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error)
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadData()
}

function resetFilters() {
  dateRange.value = null
  machineName.value = null
  userName.value = null
  agentName.value = null
  roleName.value = null
  modelName.value = null
  status.value = null
  keyword.value = ''
  page.value = 1
  loadData()
}

function exportCsv() {
  const query = buildParams()
  window.location.href = `/api/statistics/export.csv?${query}`
}

function renderChart() {
  if (!chartElement.value) return
  if (!chart) chart = echarts.init(chartElement.value, resolved.value)
  const textColor = resolved.value === 'dark' ? '#d7dde8' : '#475569'
  const lineColor = resolved.value === 'dark' ? '#354052' : '#e2e8f0'
  chart.setOption({
    backgroundColor: 'transparent',
    color: ['#4f8cff', '#8b5cf6', '#14b8a6'],
    tooltip: { trigger: 'axis' },
    legend: {
      textStyle: { color: textColor },
      data: [
        t('statistics.requestCount'),
        t('statistics.aiCallCount'),
        t('statistics.toolCallCount'),
      ],
    },
    grid: { left: 44, right: 24, top: 42, bottom: 32 },
    xAxis: {
      type: 'category',
      data: summary.value.trend.map((item) => item.day),
      axisLabel: { color: textColor },
      axisLine: { lineStyle: { color: lineColor } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: textColor },
      splitLine: { lineStyle: { color: lineColor } },
    },
    series: [
      {
        name: t('statistics.requestCount'),
        type: 'line',
        smooth: true,
        data: summary.value.trend.map((item) => item.requestCount),
      },
      {
        name: t('statistics.aiCallCount'),
        type: 'line',
        smooth: true,
        data: summary.value.trend.map((item) => item.aiCallCount),
      },
      {
        name: t('statistics.toolCallCount'),
        type: 'line',
        smooth: true,
        data: summary.value.trend.map((item) => item.toolCallCount),
      },
    ],
  }, true)
  chart.resize()
}

watch(
  () => props.show,
  (show) => {
    if (show) initialize()
  }
)
watch([resolved, locale], async () => {
  if (!props.show) return
  chart?.dispose()
  chart = null
  await nextTick()
  renderChart()
})
watch([page, pageSize], loadData)
onBeforeUnmount(() => chart?.dispose())
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="t('statistics.title')"
    class="statistics-modal"
    :bordered="false"
    :mask-closable="false"
    @update:show="emit('update:show', $event)"
  >
    <div class="statistics-content">
      <div class="statistics-filters">
        <NDatePicker v-model:value="dateRange" type="datetimerange" clearable class="filter-wide" />
        <NSelect v-model:value="machineName" :options="machineOptions" :placeholder="t('statistics.machineName')" clearable filterable />
        <NSelect v-model:value="userName" :options="userOptions" :placeholder="t('statistics.userName')" clearable filterable />
        <NSelect v-model:value="agentName" :options="agentOptions" :placeholder="t('statistics.agentName')" clearable filterable />
        <NSelect v-model:value="roleName" :options="roleOptions" :placeholder="t('statistics.roleName')" clearable filterable />
        <NSelect v-model:value="modelName" :options="modelOptions" :placeholder="t('statistics.modelName')" clearable filterable />
        <NSelect v-model:value="status" :options="statusOptions" :placeholder="t('statistics.status')" clearable />
        <NInput v-model:value="keyword" :placeholder="t('statistics.keyword')" clearable @keyup.enter="applyFilters" />
        <div class="filter-actions">
          <NButton type="primary" @click="applyFilters">{{ t('statistics.query') }}</NButton>
          <NButton @click="resetFilters">{{ t('statistics.reset') }}</NButton>
          <NButton @click="exportCsv">{{ t('statistics.exportCsv') }}</NButton>
        </div>
      </div>

      <NAlert v-if="errorText" type="error" :title="t('statistics.loadFailed')" closable @close="errorText = ''">
        {{ errorText }}
      </NAlert>

      <NSpin :show="loading">
        <div class="statistics-metrics">
          <div v-for="metric in metrics" :key="metric.label" class="metric-card">
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
          </div>
        </div>

        <section class="statistics-panel">
          <h3>{{ t('statistics.trend') }}</h3>
          <div v-if="summary.trend.length" ref="chartElement" class="statistics-chart"></div>
          <div v-else class="statistics-empty">{{ t('statistics.noData') }}</div>
        </section>

        <section class="statistics-panel statistics-table-panel">
          <h3>{{ t('statistics.details') }}</h3>
          <NDataTable
            :columns="columns"
            :data="records"
            :row-key="(row: StatisticsRecord) => row.id"
            :bordered="false"
            :single-line="false"
            :scroll-x="1630"
            :max-height="360"
          />
          <div class="statistics-pagination">
            <span>{{ t('statistics.totalRecords', { count: total }) }}</span>
            <NPagination
              v-model:page="page"
              v-model:page-size="pageSize"
              :item-count="total"
              :page-sizes="[20, 50, 100]"
              show-size-picker
              @update:page-size="page = 1"
            />
          </div>
        </section>
      </NSpin>
    </div>
  </NModal>
</template>

<style scoped>
:global(.statistics-modal) {
  width: min(96vw, 1480px);
  height: min(92vh, 960px);
}

:global(.statistics-modal > .n-card__content) {
  overflow: hidden;
}

.statistics-content {
  height: 100%;
  overflow-y: auto;
  padding-right: 4px;
  color: var(--ui-text-primary);
}

.statistics-filters {
  display: grid;
  grid-template-columns: minmax(280px, 1.6fr) repeat(7, minmax(120px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.statistics-filters > :nth-child(9) {
  grid-column: span 2;
}

.filter-actions {
  grid-column: span 6;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.statistics-metrics {
  display: grid;
  grid-template-columns: repeat(8, minmax(120px, 1fr));
  gap: 10px;
  margin: 14px 0;
}

.metric-card,
.statistics-panel {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: 12px;
}

.metric-card {
  min-height: 82px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.metric-card span {
  color: var(--ui-text-muted);
  font-size: 12px;
}

.metric-card strong {
  color: var(--ui-text-primary);
  font-size: 24px;
}

.statistics-panel {
  margin-top: 12px;
  padding: 14px;
}

.statistics-panel h3 {
  margin: 0 0 10px;
  font-size: 14px;
}

.statistics-chart {
  height: 250px;
}

.statistics-empty {
  height: 120px;
  display: grid;
  place-items: center;
  color: var(--ui-text-muted);
}

.statistics-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
  color: var(--ui-text-muted);
  font-size: 12px;
}

@media (max-width: 1100px) {
  .statistics-filters {
    grid-template-columns: repeat(3, minmax(140px, 1fr));
  }

  .statistics-filters > :nth-child(9),
  .filter-actions {
    grid-column: span 3;
  }

  .statistics-metrics {
    grid-template-columns: repeat(4, minmax(120px, 1fr));
  }
}

@media (max-width: 700px) {
  .statistics-filters {
    grid-template-columns: 1fr;
  }

  .statistics-filters > :nth-child(9),
  .filter-actions {
    grid-column: auto;
  }

  .filter-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .statistics-metrics {
    grid-template-columns: repeat(2, minmax(110px, 1fr));
  }
}
</style>

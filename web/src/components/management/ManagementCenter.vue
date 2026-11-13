<script setup lang="ts">
import { computed, type Component } from 'vue'
import { CloseOutline } from '@vicons/ionicons5'
import { NButton, NIcon, NModal } from 'naive-ui'

export interface ManagementMenuItem {
  key: string
  label: string
  group: string
  icon: Component
}

const props = defineProps<{
  show: boolean
  section: string
  title: string
  items: ManagementMenuItem[]
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  'update:section': [value: string]
}>()

const groups = computed(() => {
  const grouped = new Map<string, ManagementMenuItem[]>()
  for (const item of props.items) {
    const entries = grouped.get(item.group) ?? []
    entries.push(item)
    grouped.set(item.group, entries)
  }
  return [...grouped.entries()].map(([label, items]) => ({ label, items }))
})

const activeLabel = computed(
  () => props.items.find((item) => item.key === props.section)?.label ?? ''
)
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    :close-on-esc="true"
    @update:show="emit('update:show', $event)"
  >
    <div class="management-center" role="dialog" :aria-label="title">
      <header class="management-center__header">
        <div>
          <strong>{{ title }}</strong>
          <span>{{ activeLabel }}</span>
        </div>
        <NButton quaternary circle size="small" :aria-label="'Close'" @click="emit('update:show', false)">
          <template #icon><NIcon :component="CloseOutline" /></template>
        </NButton>
      </header>

      <div class="management-center__body">
        <nav class="management-center__nav" :aria-label="title">
          <div v-for="group in groups" :key="group.label" class="management-menu-group">
            <div class="management-menu-group__label">{{ group.label }}</div>
            <button
              v-for="item in group.items"
              :key="item.key"
              type="button"
              class="management-menu-item"
              :class="{ 'management-menu-item--active': item.key === section }"
              @click="emit('update:section', item.key)"
            >
              <NIcon :component="item.icon" />
              <span>{{ item.label }}</span>
            </button>
          </div>
        </nav>

        <main class="management-center__content">
          <slot />
        </main>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.management-center {
  --canvas: var(--ui-canvas);
  --surface: var(--ui-surface);
  --muted: var(--ui-surface-muted);
  --border: var(--ui-border);
  --text: var(--ui-text);
  --subtle: var(--ui-text-muted);
  display: flex;
  width: min(1180px, 94vw);
  height: min(820px, 90vh);
  overflow: hidden;
  flex-direction: column;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 18px 60px rgba(15, 23, 42, .24);
}

.management-center__header {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px 0 20px;
  border-bottom: 1px solid var(--border);
}

.management-center__header > div {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.management-center__header strong {
  font-size: 16px;
}

.management-center__header span {
  color: var(--subtle);
  font-size: 12px;
}

.management-center__body {
  display: flex;
  min-height: 0;
  flex: 1;
}

.management-center__nav {
  width: 220px;
  flex: 0 0 220px;
  overflow-y: auto;
  padding: 14px 10px;
  background: var(--canvas);
  border-right: 1px solid var(--border);
}

.management-menu-group + .management-menu-group {
  margin-top: 16px;
}

.management-menu-group__label {
  padding: 0 10px 5px;
  color: var(--subtle);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .04em;
}

.management-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  color: var(--text);
  background: transparent;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;
}

.management-menu-item:hover {
  background: var(--muted);
}

.management-menu-item--active {
  color: var(--ui-primary);
  background: var(--ui-primary-soft);
  font-weight: 600;
}

.management-menu-item .n-icon {
  flex: 0 0 auto;
  font-size: 17px;
}

.management-center__content {
  min-width: 0;
  overflow-y: auto;
  flex: 1;
  padding: 20px 22px 28px;
}

@media (max-width: 900px) {
  .management-center {
    width: 100vw;
    height: 100dvh;
    border: 0;
    border-radius: 0;
  }

  .management-center__body {
    flex-direction: column;
  }

  .management-center__nav {
    display: flex;
    width: 100%;
    flex: 0 0 auto;
    gap: 6px;
    overflow-x: auto;
    padding: 8px 12px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .management-menu-group {
    display: contents;
  }

  .management-menu-group + .management-menu-group {
    margin-top: 0;
  }

  .management-menu-group__label {
    display: none;
  }

  .management-menu-item {
    width: auto;
    flex: 0 0 auto;
    white-space: nowrap;
  }

  .management-center__content {
    padding: 16px;
  }
}
</style>

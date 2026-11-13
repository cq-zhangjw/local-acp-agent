<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { darkTheme, lightTheme, NConfigProvider, NMessageProvider, type GlobalThemeOverrides } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { initializeTheme, useTheme } from './theme'

const { locale } = useI18n()
const { resolved } = useTheme()
let stopTheme = () => undefined

// 统一使用中文字体栈显示中/日/英界面（中日文字形覆盖更好，英文也完全可读）
const fontFamily = '"PingFang SC", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif'

const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    primaryColor: resolved.value === 'dark' ? '#76a7ff' : '#2563eb',
    primaryColorHover: resolved.value === 'dark' ? '#9ac0ff' : '#1d4ed8',
    primaryColorPressed: resolved.value === 'dark' ? '#5d91e8' : '#1e40af',
    borderRadius: '8px',
    fontFamily
  }
}))
const naiveTheme = computed(() => resolved.value === 'dark' ? darkTheme : lightTheme)

function applyBodyFont() {
  document.body.style.fontFamily = fontFamily
}

watch(locale, applyBodyFont)
onMounted(applyBodyFont)
onMounted(() => { stopTheme = initializeTheme() })
onBeforeUnmount(() => stopTheme())
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <RouterView />
    </NMessageProvider>
  </NConfigProvider>
</template>

import { ref, watch, type Ref } from 'vue'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const themeStorageKey = 'acp-ui-theme'

function isThemePreference(value: string | null): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}

function readStoredPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(themeStorageKey)
    return isThemePreference(stored) ? stored : 'system'
  } catch (error) {
    console.warn('[theme] Unable to read the stored theme preference.', error)
    return 'system'
  }
}

function systemTheme(): ResolvedTheme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export const themePreference = ref<ThemePreference>(readStoredPreference())
export const resolvedTheme = ref<ResolvedTheme>('light')

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === 'system' ? systemTheme() : preference
}

export function applyTheme(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference)
  resolvedTheme.value = resolved
  document.documentElement.dataset.theme = resolved
  document.documentElement.style.colorScheme = resolved
  return resolved
}

export function setThemePreference(preference: ThemePreference) {
  themePreference.value = preference
  try {
    localStorage.setItem(themeStorageKey, preference)
  } catch (error) {
    console.warn('[theme] Unable to persist the theme preference.', error)
  }
  applyTheme(preference)
  broadcastTheme(preference, resolvedTheme.value)
}

export function watchSystemTheme(callback: (theme: ResolvedTheme) => void): () => void {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = () => {
    if (themePreference.value === 'system') {
      const theme = resolveTheme('system')
      applyTheme('system')
      callback(theme)
    }
  }
  media.addEventListener?.('change', onChange)
  return () => media.removeEventListener?.('change', onChange)
}

const themeChannel = typeof window !== 'undefined' && 'BroadcastChannel' in window
  ? new BroadcastChannel('acp-ui-theme')
  : null

function broadcastTheme(preference: ThemePreference, theme: ResolvedTheme) {
  themeChannel?.postMessage({ type: 'theme-changed', preference, resolvedTheme: theme })
}

export function initializeTheme(): () => void {
  applyTheme(themePreference.value)
  const stopSystemWatch = watchSystemTheme(() => undefined)
  const onStorage = (event: StorageEvent) => {
    if (event.key !== themeStorageKey || !isThemePreference(event.newValue)) return
    themePreference.value = event.newValue
    applyTheme(event.newValue)
  }
  const onChannelMessage = (event: MessageEvent) => {
    const data = event.data as { type?: string; preference?: string }
    if (data?.type !== 'theme-changed' || !isThemePreference(data.preference ?? null)) return
    themePreference.value = data.preference
    applyTheme(data.preference)
  }
  window.addEventListener('storage', onStorage)
  themeChannel?.addEventListener('message', onChannelMessage)
  return () => {
    stopSystemWatch()
    window.removeEventListener('storage', onStorage)
    themeChannel?.removeEventListener('message', onChannelMessage)
  }
}

export function useTheme(): {
  preference: Ref<ThemePreference>
  resolved: Ref<ResolvedTheme>
  setPreference: typeof setThemePreference
} {
  return { preference: themePreference, resolved: resolvedTheme, setPreference: setThemePreference }
}

watch(themePreference, (preference) => applyTheme(preference), { immediate: true })

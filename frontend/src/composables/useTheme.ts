import { ref } from 'vue'

const STORAGE_KEY = 'litetrain.theme'
const isDark = ref(false)
let initialized = false

function apply(dark: boolean) {
  isDark.value = dark
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  const meta = document.querySelector('meta[name="theme-color"]')
  meta?.setAttribute('content', dark ? '#07130f' : '#0d1f1a')
}

export function initTheme() {
  if (initialized) return
  initialized = true
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'dark') {
    apply(true)
  } else if (saved === 'light') {
    apply(false)
  } else {
    apply(window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false)
  }
}

export function useTheme() {
  if (!initialized && typeof window !== 'undefined') initTheme()

  function toggleTheme() {
    const next = !isDark.value
    apply(next)
    localStorage.setItem(STORAGE_KEY, next ? 'dark' : 'light')
  }

  return { isDark, toggleTheme }
}

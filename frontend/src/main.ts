import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/styles/index.css'

import { initTheme } from '@/composables/useTheme'

initTheme()

async function bootstrap() {
  const adminMode = import.meta.env.VITE_ADMIN_MODE === 'true'
  const rootComponent = adminMode
    ? (await import('@/views/AdminApp.vue')).default
    : (await import('./App.vue')).default
  const app = createApp(rootComponent)

  for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(name, component)
  }

  if (!adminMode) {
    const router = (await import('./router')).default
    app.use(router)
  }

  app.use(ElementPlus, { locale: zhCn })
  app.mount('#app')
}

void bootstrap()

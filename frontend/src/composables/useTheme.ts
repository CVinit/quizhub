import { onMounted, watch } from 'vue'
import { useSiteStore } from '@/stores/site'
import router from '@/router'

const hexToRgb = (color: string) => ({
  r: parseInt(color.slice(1, 3), 16),
  g: parseInt(color.slice(3, 5), 16),
  b: parseInt(color.slice(5, 7), 16),
})

/** 与白色/黑色按比例混合（p=0 为原色，1 为纯白/纯黑）。 */
const mix = (c: { r: number; g: number; b: number }, target: 'white' | 'black', p: number) => {
  const t = target === 'white' ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 }
  const ch = (a: number, b: number) => Math.round(a + (b - a) * p)
  return `#${[ch(c.r, t.r), ch(c.g, t.g), ch(c.b, t.b)].map((v) => v.toString(16).padStart(2, '0')).join('')}`
}

/** 注入 Element Plus 主题色及其浅色/深色梯度。 */
const apply = (color: string) => {
  if (!color || !/^#[0-9a-fA-F]{6}$/.test(color)) return
  const rgb = hexToRgb(color)
  const root = document.documentElement
  root.style.setProperty('--brand-primary', color)
  root.style.setProperty('--brand-primary-light', mix(rgb, 'white', 0.2))
  root.style.setProperty('--brand-primary-dark', mix(rgb, 'black', 0.2))
  root.style.setProperty('--brand-primary-light-9', mix(rgb, 'white', 0.9))
  root.style.setProperty('--el-color-primary', color)
  // Element Plus 的各级浅色/深色
  for (let i = 1; i <= 9; i++) {
    root.style.setProperty(`--el-color-primary-light-${i}`, mix(rgb, 'white', i / 10))
    root.style.setProperty(`--el-color-primary-dark-${i}`, mix(rgb, 'black', i / 10))
  }
}

/**
 * 从后端读取站点信息并注入主题色、站点名、Logo。
 *
 * 数据加载统一委托 site store（含并发去重），本 composable 只负责“store 值 → DOM/标题”
 * 的同步，避免站点字段的默认值与兜底逻辑在两处各写一份。
 */
export function useTheme() {
  const site = useSiteStore()

  /** 拉取站点信息（委托 store；并发调用共享同一次请求）。 */
  const loadFromServer = () => site.load()

  // brand_color 就绪（或被设置页更新）后即时生效
  watch(() => site.brand_color, apply, { immediate: true })
  // 站点名就绪后按 `页面名 · 站点名` 刷新标题（router.afterEach 已先写入默认名）
  watch(
    () => site.site_name,
    (name) => {
      const page = router.currentRoute.value.meta.title as string | undefined
      document.title = page ? `${page} · ${name}` : name
    },
  )

  onMounted(loadFromServer)
  return { apply, loadFromServer }
}

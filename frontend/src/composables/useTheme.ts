import { onMounted } from 'vue'
import { api } from '@/api/http'
import { useSiteStore } from '@/stores/site'

const hexToRgb = (color: string) => ({
  r: parseInt(color.slice(1, 3), 16),
  g: parseInt(color.slice(3, 5), 16),
  b: parseInt(color.slice(5, 7), 16),
})

/** 与白色/黑色按比例混合（p=0 为原色，1 为纯白/纯黑）。 */
const mix = (c: { r: number; g: number; b: number }, target: 'white' | 'black', p: number) => {
  const t = target === 'white' ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 }
  const ch = (a: number, b: number) => Math.round(a + (b - a) * p)
  return `#${[ch(c.r, t.r), ch(c.g, t.g), ch(c.b, t.b)]
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')}`
}

/** 从后端读取站点信息：注入主题色、站点名、Logo（写入 site store 共享）。 */
export function useTheme() {
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

  const loadFromServer = async () => {
    try {
      const data = await api.get<{ site_name: string; brand_color: string; site_logo: string; rank_visible: boolean }>('/system/site')
      const site = useSiteStore()
      site.site_name = data.site_name || '培训考试平台'
      site.site_logo = data.site_logo || ''
      site.brand_color = data.brand_color || '#E60012'
      site.rank_visible = data.rank_visible !== false
      site.loaded = true
      if (data.brand_color) apply(data.brand_color)
      if (data.site_name) document.title = data.site_name
    } catch { /* 忽略，用默认主题 */ }
  }

  onMounted(loadFromServer)
  return { apply, loadFromServer }
}

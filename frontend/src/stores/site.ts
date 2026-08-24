import { defineStore } from 'pinia'
import { api } from '@/api/http'

interface SiteInfo {
  site_name: string
  site_logo: string
  brand_color: string
  rank_visible: boolean
}

/** 站点信息（站点名、Logo、主题色），全应用共享，仅拉取一次。 */
export const useSiteStore = defineStore('site', {
  state: () => ({
    site_name: '培训考试平台',
    site_logo: '',
    brand_color: '#E60012',
    rank_visible: true,
    loaded: false,
  }),
  getters: {
    // Logo 首字回退（无 Logo 时用站点名首字作方块标识）
    logoChar: (s) => s.site_name.slice(0, 1) || '培',
  },
  actions: {
    async load() {
      if (this.loaded) return
      await this.refresh()
    },
    /** 强制重新拉取站点设置（绕过 loaded 缓存，用于需要实时性的场景如排行可见性校验）。 */
    async refresh() {
      try {
        const data = await api.get<SiteInfo>('/system/site')
        this.site_name = data.site_name || '培训考试平台'
        this.site_logo = data.site_logo || ''
        this.brand_color = data.brand_color || '#E60012'
        this.rank_visible = data.rank_visible !== false
      } catch { /* 忽略，用默认 */ }
      this.loaded = true
    },
    /** Logo 上传成功后本地同步（避免重新拉取）。 */
    setLogo(url: string) {
      this.site_logo = url
    },
  },
})

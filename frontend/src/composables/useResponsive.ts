/**
 * 响应式断点工具。
 * - mobile  : < 768px（手机）
 * - tablet  : 768–1199px（平板）
 * - desktop : >= 1200px（桌面）
 *
 * 基于 matchMedia，与 CSS @media 断点保持一致（见 assets/main.css）。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

export const BP_MOBILE = '(max-width: 767px)'
export const BP_TABLET = '(min-width: 768px) and (max-width: 1199px)'
export const BP_DESKTOP = '(min-width: 1200px)'

export function useResponsive() {
  // 首帧同步求值，保证依赖 isMobile 的初始渲染（如答题卡展开态）不闪烁
  const hasMql = typeof window !== 'undefined' && !!window.matchMedia
  const isMobile = ref(hasMql && window.matchMedia(BP_MOBILE).matches)
  const isTablet = ref(hasMql && window.matchMedia(BP_TABLET).matches)
  const isDesktop = ref(hasMql && window.matchMedia(BP_DESKTOP).matches)

  let mqlMobile: MediaQueryList | null = null
  let mqlTablet: MediaQueryList | null = null
  let mqlDesktop: MediaQueryList | null = null

  const onMobile = (e: MediaQueryListEvent) => {
    isMobile.value = e.matches
  }
  const onTablet = (e: MediaQueryListEvent) => {
    isTablet.value = e.matches
  }
  const onDesktop = (e: MediaQueryListEvent) => {
    isDesktop.value = e.matches
  }

  onMounted(() => {
    if (!hasMql) return
    mqlMobile = window.matchMedia(BP_MOBILE)
    mqlTablet = window.matchMedia(BP_TABLET)
    mqlDesktop = window.matchMedia(BP_DESKTOP)
    mqlMobile.addEventListener('change', onMobile)
    mqlTablet.addEventListener('change', onTablet)
    mqlDesktop.addEventListener('change', onDesktop)
  })

  onBeforeUnmount(() => {
    mqlMobile?.removeEventListener('change', onMobile)
    mqlTablet?.removeEventListener('change', onTablet)
    mqlDesktop?.removeEventListener('change', onDesktop)
  })

  return { isMobile, isTablet, isDesktop }
}

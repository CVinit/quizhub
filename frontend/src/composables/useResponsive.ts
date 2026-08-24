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
  const isMobile = ref(false)
  const isTablet = ref(false)
  const isDesktop = ref(true)

  let mqlMobile: MediaQueryList | null = null
  let mqlTablet: MediaQueryList | null = null
  let mqlDesktop: MediaQueryList | null = null

  const onMobile = (e: MediaQueryListEvent) => { isMobile.value = e.matches }
  const onTablet = (e: MediaQueryListEvent) => { isTablet.value = e.matches }
  const onDesktop = (e: MediaQueryListEvent) => { isDesktop.value = e.matches }

  onMounted(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    mqlMobile = window.matchMedia(BP_MOBILE)
    mqlTablet = window.matchMedia(BP_TABLET)
    mqlDesktop = window.matchMedia(BP_DESKTOP)
    isMobile.value = mqlMobile.matches
    isTablet.value = mqlTablet.matches
    isDesktop.value = mqlDesktop.matches
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

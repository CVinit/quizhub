import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { api } from '@/api/http'

/**
 * 长表单草稿自动保存：每 10s 保存到后端 + localStorage 兜底。
 * form_key 用于区分不同表单。
 */
export function useDraft(formKey: string, source: Ref<any>, opts?: { interval?: number }) {
  const interval = opts?.interval ?? 10000
  const restored = ref<any>(null)
  let timer: any = null
  let loaded = false

  const save = async (silent = true) => {
    const payload = JSON.parse(JSON.stringify(source.value || {}))
    // localStorage 兜底
    try {
      localStorage.setItem(`draft:${formKey}`, JSON.stringify(payload))
    } catch { /* ignore */ }
    // 后端持久化
    try {
      await api.put(`/drafts/${formKey}`, payload)
    } catch { /* ignore */ }
  }

  const load = async () => {
    if (loaded) return
    loaded = true
    try {
      const res = await api.get(`/drafts/${formKey}`)
      if (res.exists && res.payload && Object.keys(res.payload).length) {
        restored.value = res.payload
      } else {
        // localStorage 兜底
        const local = localStorage.getItem(`draft:${formKey}`)
        if (local) restored.value = JSON.parse(local)
      }
    } catch { /* ignore */ }
  }

  const clear = async () => {
    try {
      await api.delete(`/drafts/${formKey}`)
    } catch { /* ignore */ }
    localStorage.removeItem(`draft:${formKey}`)
  }

  onMounted(() => {
    load()
    timer = setInterval(() => save(true), interval)
    window.addEventListener('beforeunload', beforeUnload)
  })

  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
    save(true)
    window.removeEventListener('beforeunload', beforeUnload)
  })

  // 表单值变化触发保存（防抖由 interval 兜底，这里仅用于离开提示标记）
  const dirty = ref(false)
  watch(source, () => { dirty.value = true }, { deep: true })

  function beforeUnload(e: BeforeUnloadEvent) {
    if (dirty.value) {
      e.preventDefault()
      e.returnValue = ''
    }
  }

  return { restored, save, clear, dirty }
}

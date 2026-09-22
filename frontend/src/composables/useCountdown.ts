import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export interface CountdownApi {
  /** 剩余秒数（0 表示已到期） */
  seconds: Ref<number>
  running: Ref<boolean>
  /** 从「现在 + fromSeconds」开始（或重启）倒计时 */
  start: (fromSeconds: number) => void
  /** 停止并清零 */
  stop: () => void
  /** 替换「回到前台」回调（默认只按本地 deadline 校准）。 */
  setOnVisible: (handler: () => void) => void
}

/**
 * 截止时间驱动的倒计时。
 *
 * 不按定时器回调次数递减：浏览器会把后台标签页的 `setInterval` 节流到最低约 1 次/分钟，
 * 按次数递减会让倒计时远慢于真实时间（考试场景等于白送时间）。这里记录 deadline，
 * 每次 tick 用 `Date.now()` 重算，并在标签页重新可见时立刻校准。
 *
 * `onExpire` 只会触发一次（到期后自动停止）；组件卸载时自动清理。
 */
export function useCountdown(onExpire?: () => void): CountdownApi {
  const seconds = ref(0)
  const running = ref(false)
  let timer: ReturnType<typeof setInterval> | undefined
  let deadline = 0

  const stop = () => {
    if (timer) clearInterval(timer)
    timer = undefined
    running.value = false
    // 与接口注释「停止并清零」一致：否则交卷/结束后标题栏仍显示冻结的剩余时间
    seconds.value = 0
  }

  const tick = () => {
    if (!running.value) return
    const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
    seconds.value = left
    if (left <= 0) {
      stop()
      onExpire?.()
    }
  }

  const start = (fromSeconds: number) => {
    if (timer) clearInterval(timer)
    timer = undefined
    const total = Math.max(0, Math.floor(fromSeconds))
    deadline = Date.now() + total * 1000
    seconds.value = total
    running.value = true
    if (total <= 0) {
      running.value = false
      onExpire?.()
      return
    }
    timer = setInterval(tick, 1000)
  }

  // 回到前台立刻校准：后台节流期间不会丢失时间。
  // onVisible 可由调用方替换，以便考试页在校准时向服务端核对剩余时间。
  let onVisible = () => {
    if (document.visibilityState === 'visible') tick()
  }
  const setOnVisible = (handler: () => void) => {
    onVisible = handler
  }

  const handleVisible = () => onVisible()

  onMounted(() => document.addEventListener('visibilitychange', handleVisible))
  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('visibilitychange', handleVisible)
  })

  return { seconds, running, start, stop, setOnVisible }
}

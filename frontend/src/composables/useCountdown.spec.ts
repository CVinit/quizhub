import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { useCountdown } from '@/composables/useCountdown'

let api: ReturnType<typeof useCountdown>
let expired = 0

const Harness = defineComponent({
  props: { from: { type: Number, required: true } },
  setup(props) {
    expired = 0
    api = useCountdown(() => {
      expired += 1
    })
    api.start(props.from)
    return () => h('span', String(api.seconds.value))
  },
})

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useCountdown', () => {
  it('按截止时间递减，而不是按定时器回调次数', () => {
    const wrapper = mount(Harness, { props: { from: 3 } })
    expect(api.seconds.value).toBe(3)
    vi.advanceTimersByTime(1000)
    expect(api.seconds.value).toBe(2)
    vi.advanceTimersByTime(1000)
    expect(api.seconds.value).toBe(1)
    wrapper.unmount()
  })

  it('后台节流后按真实时间校准（不因少跑回调而多给时间）', () => {
    const wrapper = mount(Harness, { props: { from: 300 } })
    // 模拟标签页被挂起：5 分钟内一次回调都没跑，随后一次性补上时间
    vi.advanceTimersByTime(60_000)
    expect(api.seconds.value).toBe(240)
    wrapper.unmount()
  })

  it('一次回调都没跑时，重新可见会按截止时间校准（防「按回调次数递减」的实现回归）', () => {
    // advanceTimersByTime 会把 60 次 interval 回调全部跑完，因此旧的「按回调次数递减」实现
    // 也能通过上一个用例。这里只推进系统时间、不触发任何回调，再手动派发 visibilitychange：
    // 只有真正按 deadline 计算的实现才会把剩余时间校准为 240。
    const wrapper = mount(Harness, { props: { from: 300 } })
    expect(api.seconds.value).toBe(300)

    vi.setSystemTime(Date.now() + 60_000)
    // 未派发事件前：没有任何回调运行，剩余秒数保持原值
    expect(api.seconds.value).toBe(300)

    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(api.seconds.value).toBe(240)

    wrapper.unmount()
  })

  it('setOnVisible 后由调用方接管校准（考试页据此向服务端核对剩余时间）', () => {
    const wrapper = mount(Harness, { props: { from: 300 } })
    let calls = 0
    api.setOnVisible(() => {
      calls += 1
    })
    vi.setSystemTime(Date.now() + 60_000)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    // 自定义回调接管后不再走默认的本地 tick，剩余秒数保持原值，由调用方自行重设
    expect(calls).toBe(1)
    expect(api.seconds.value).toBe(300)
    wrapper.unmount()
  })

  it('不可见时派发 visibilitychange 不会校准（避免误判为回到前台）', () => {
    const wrapper = mount(Harness, { props: { from: 300 } })
    vi.setSystemTime(Date.now() + 60_000)
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(api.seconds.value).toBe(300)
    wrapper.unmount()
  })

  it('到期后停止计时，且 onExpire 只触发一次', () => {
    const wrapper = mount(Harness, { props: { from: 2 } })
    vi.advanceTimersByTime(5000)
    expect(expired).toBe(1)
    expect(api.seconds.value).toBe(0)
    expect(api.running.value).toBe(false)

    vi.advanceTimersByTime(5000)
    expect(expired).toBe(1)
    wrapper.unmount()
  })

  it('start(0) 立即到期并触发一次', () => {
    const wrapper = mount(Harness, { props: { from: 0 } })
    expect(expired).toBe(1)
    expect(api.running.value).toBe(false)
    wrapper.unmount()
  })

  it('stop() 停止计时并清零剩余秒数', () => {
    const wrapper = mount(Harness, { props: { from: 100 } })
    api.stop()
    expect(api.running.value).toBe(false)
    expect(api.seconds.value).toBe(0)
    vi.advanceTimersByTime(5000)
    expect(expired).toBe(0)
    wrapper.unmount()
  })

  it('start 中途重启不会把剩余秒数清零（服务端校准失败时不该停表）', () => {
    const wrapper = mount(Harness, { props: { from: 300 } })
    vi.advanceTimersByTime(60_000)
    expect(api.seconds.value).toBe(240)
    api.start(240)
    expect(api.seconds.value).toBe(240)
    expect(api.running.value).toBe(true)
    wrapper.unmount()
  })

  it('重新 start 会用新的倒计时覆盖旧的', () => {
    const wrapper = mount(Harness, { props: { from: 100 } })
    api.start(10)
    expect(api.seconds.value).toBe(10)
    vi.advanceTimersByTime(1000)
    expect(api.seconds.value).toBe(9)
    wrapper.unmount()
  })

  it('组件卸载时移除 visibilitychange 监听', () => {
    const removeSpy = vi.spyOn(document, 'removeEventListener')
    const wrapper = mount(Harness, { props: { from: 100 } })
    wrapper.unmount()
    expect(removeSpy).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
    removeSpy.mockRestore()
  })
})

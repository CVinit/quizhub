/**
 * 练习答题页的键盘快捷键。
 *
 * A–J / 1–9 选择选项（单选/多选/判断），Enter 提交或进入下一题，←→ 切题。
 * 焦点在输入框/可编辑区域时全部忽略；焦点在按钮上（选项本身是 <button>）时只忽略 Enter：
 * 选项点击后焦点会留在按钮上，Enter 由按钮原生点击触发，若这里再处理一次，
 * 会先提交“上一次的选择”再改变选中项。字母/数字选择与 ←→ 切题在按钮上仍然生效。
 */
import { onMounted, onUnmounted } from 'vue'

export interface AnswerKeyboardOptions {
  /** 当前题型；无题时为 undefined */
  currentType: () => string | undefined
  /** 当前题可选项数量 */
  optionCount: () => number
  isLoading: () => boolean
  isShowingResult: () => boolean
  /** 是否需要在提交后才进入下一题（客观题） */
  needSubmit: () => boolean
  pick: (value: string) => void
  toggleMulti: (letter: string) => void
  submit: () => void
  next: () => void
  prev: () => void
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const CHOICE_TYPES = ['单选题', '多选题', '判断题']

export function useAnswerKeyboard(opts: AnswerKeyboardOptions) {
  const onKeydown = (e: KeyboardEvent) => {
    const t = e.target as HTMLElement | null
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
    if (e.ctrlKey || e.metaKey || e.altKey) return
    // 焦点在按钮上时 Enter 交给按钮的原生点击，避免重复触发提交
    const onButton = !!t && t.tagName === 'BUTTON'
    const type = opts.currentType()
    if (!type || opts.isLoading()) return

    if (CHOICE_TYPES.includes(type)) {
      let idx = LETTERS.indexOf(e.key.toUpperCase())
      if (idx < 0 && /^[1-9]$/.test(e.key)) idx = Number(e.key) - 1
      if (idx >= 0) {
        if (type === '判断题') {
          if (idx <= 1) opts.pick(idx === 0 ? '正确' : '错误')
          return
        }
        if (idx < opts.optionCount()) {
          if (type === '单选题') opts.pick(LETTERS[idx])
          else opts.toggleMulti(LETTERS[idx])
        }
        return
      }
    }
    if (e.key === 'Enter') {
      if (onButton) return
      if (opts.isShowingResult()) opts.next()
      else if (type !== '拖拽题') opts.submit()
      return
    }
    if (e.key === 'ArrowLeft') {
      opts.prev()
      return
    }
    if (e.key === 'ArrowRight' && (opts.isShowingResult() || !opts.needSubmit())) opts.next()
  }

  onMounted(() => window.addEventListener('keydown', onKeydown))
  onUnmounted(() => window.removeEventListener('keydown', onKeydown))
}

import { reactive, ref } from 'vue'
import type { QuestionAnswer } from '@/api/practice'
import { shuffle } from '@/utils/array'

/** 作答区所需的题目字段（练习题与考试题的交集）。 */
export interface DraftQuestion {
  id: number
  type: string
  question: string
  left_items?: string[] | null
  right_items?: string[] | null
}

export interface ApplyAnswerOptions {
  /** 拖拽题左侧题项是否打乱（练习页打乱、考试页保持卷面顺序） */
  randomizeLeft?: boolean
}

/**
 * 练习页与考试页共用的作答状态与纯转换。
 *
 * 两页的提交时机差别很大（练习页逐题判分、考试页带乐观锁的自动保存），
 * 但「六种题型的交互状态 ⇄ 提交载荷」的映射完全一致。此前各写一份，
 * 修一处漏一处（例如考试页还原答案时把简答字符串写进了单选字段，
 * 回来看到的简答框是空的）。这里只收敛状态与转换，不碰各自的网络/计时逻辑。
 */
export function useAnswerDraft() {
  const picked = ref('') // 单选/判断
  const multiPicked = ref<string[]>([]) // 多选（字母）
  const blanks = ref<string[]>([]) // 填空（每空一项）
  const shortAns = ref('')
  const dragMap = reactive<Record<string, string>>({}) // 拖拽：right -> left
  const shuffledLeft = ref<string[]>([]) // 拖拽：左侧展示顺序
  const draggingItem = ref('')

  /** 填空题空位数：题干里连续两个以上的下划线；没有标记时按 1 空处理。 */
  const blankCount = (q: DraftQuestion | undefined): number => q?.question.match(/_{2,}/g)?.length || 1

  const emptyBlanks = (q: DraftQuestion | undefined): string[] => Array.from({ length: blankCount(q) }, () => '')

  /** 清空作答区（切题时先调用）。 */
  const reset = () => {
    picked.value = ''
    multiPicked.value = []
    blanks.value = []
    shortAns.value = ''
    Object.keys(dragMap).forEach((k) => delete dragMap[k])
    draggingItem.value = ''
  }

  /** 把答案还原成交互状态（未作答、回看、刷新恢复都走这里）。 */
  const applyAnswer = (
    q: DraftQuestion | undefined,
    answer: QuestionAnswer | undefined,
    options: ApplyAnswerOptions = {},
  ) => {
    reset()
    blanks.value = emptyBlanks(q)
    const left = [...(q?.left_items || [])]
    shuffledLeft.value = options.randomizeLeft ? shuffle(left) : left
    if (answer == null) return
    if (typeof answer === 'string') {
      if (q?.type === '多选题') multiPicked.value = answer.split('').filter(Boolean)
      else if (q?.type === '简答题') shortAns.value = answer
      else picked.value = answer
      return
    }
    if (Array.isArray(answer)) {
      // QuestionAnswer 还含嵌套数组形态（后端填空题正确答案为 string[][]：每空可有多个
      // 等价答案）。取每空首个等价答案填入输入框，与 hasAnswer/QuestionResult 的口径一致；
      // 若按旧实现把非字符串元素一律写成空串，界面会显示为空，用户一动失焦就会用
      // ['新值',''] 覆盖服务端已存的答案。
      blanks.value = answer.map((x) => (Array.isArray(x) ? String(x[0] ?? '') : String(x ?? '')))
      return
    }
    Object.entries(answer as Record<string, string>).forEach(([leftItem, right]) => {
      dragMap[String(right)] = leftItem
    })
  }

  /** 交互状态 → 提交载荷（多选排序拼接、拖拽反转、填空数组、其余取文本框）。 */
  const buildPayload = (q: DraftQuestion | undefined): QuestionAnswer => {
    if (!q) return null
    switch (q.type) {
      case '多选题':
        return [...multiPicked.value].sort().join('')
      case '拖拽题': {
        const mapping: Record<string, string> = {}
        Object.entries(dragMap).forEach(([right, left]) => {
          mapping[left] = right
        })
        return mapping
      }
      case '填空题':
        return [...blanks.value]
      case '简答题':
        return shortAns.value
      default:
        return picked.value || null
    }
  }

  /**
   * 是否已作答：答题卡着色与交卷确认共用同一判定口径。
   * 多选题全部取消（''）、填空全空（['']）、拖拽未填（{}）均视为未作答。
   */
  const hasAnswer = (answer: QuestionAnswer | undefined | null): boolean => {
    if (answer === null || answer === undefined) return false
    if (typeof answer === 'string') return answer.trim() !== ''
    if (Array.isArray(answer)) {
      return answer.some((x) => (Array.isArray(x) ? x.some((y) => String(y).trim() !== '') : String(x).trim() !== ''))
    }
    return Object.keys(answer).length > 0
  }

  const toggleMulti = (letter: string) => {
    multiPicked.value = multiPicked.value.includes(letter)
      ? multiPicked.value.filter((x) => x !== letter)
      : [...multiPicked.value, letter]
  }

  const updateBlank = (index: number, value: string) => {
    blanks.value[index] = value
  }

  const setShortAns = (value: string) => {
    shortAns.value = value
  }

  /**
   * 拖拽：点击左侧题项 → 放进第一个空位。
   *
   * 必须先释放该题项原先占用的槽位（与 dropOn 同口径），否则对已放置的题项再点一次会
   * 占用下一个空位：界面显示同一项出现在两个容器里、allDragFilled() 误判为已填满，
   * 而 buildPayload 按 left→right 反转时只保留后一条映射，前一条被静默丢弃。
   */
  const pickSource = (q: DraftQuestion | undefined, item: string) => {
    Object.keys(dragMap).forEach((k) => {
      if (dragMap[k] === item) delete dragMap[k]
    })
    const empty = (q?.right_items || []).find((r) => !dragMap[r])
    if (empty) dragMap[empty] = item
  }

  const startDrag = (item: string) => {
    draggingItem.value = item
  }

  /** 拖拽：放到指定右侧容器（并解除该题项原先的占用）。 */
  const dropOn = (right: string) => {
    if (!draggingItem.value) return
    Object.keys(dragMap).forEach((k) => {
      if (dragMap[k] === draggingItem.value) delete dragMap[k]
    })
    dragMap[right] = draggingItem.value
    draggingItem.value = ''
  }

  const unassign = (right: string) => {
    delete dragMap[right]
  }

  const allDragFilled = (q: DraftQuestion | undefined) => (q?.right_items || []).every((r) => !!dragMap[r])

  return {
    picked,
    multiPicked,
    blanks,
    shortAns,
    dragMap,
    shuffledLeft,
    draggingItem,
    reset,
    applyAnswer,
    buildPayload,
    hasAnswer,
    toggleMulti,
    updateBlank,
    setShortAns,
    pickSource,
    startDrag,
    dropOn,
    unassign,
    allDragFilled,
  }
}

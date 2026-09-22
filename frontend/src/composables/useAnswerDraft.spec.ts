import { describe, expect, it } from 'vitest'
import { useAnswerDraft, type DraftQuestion } from '@/composables/useAnswerDraft'

const question = (over: Partial<DraftQuestion> = {}): DraftQuestion => ({
  id: 1,
  type: '单选题',
  question: '题干',
  ...over,
})

describe('useAnswerDraft.buildPayload', () => {
  it('无题目时返回 null', () => {
    expect(useAnswerDraft().buildPayload(undefined)).toBeNull()
  })

  it('单选题未作答返回 null（而不是空字符串）', () => {
    expect(useAnswerDraft().buildPayload(question())).toBeNull()
  })

  it('多选题答案排序后拼接，与后端判分口径一致', () => {
    const draft = useAnswerDraft()
    draft.multiPicked.value = ['C', 'A', 'B']
    expect(draft.buildPayload(question({ type: '多选题' }))).toBe('ABC')
  })

  it('填空题返回每空一项的数组', () => {
    const draft = useAnswerDraft()
    draft.blanks.value = ['甲', '乙']
    expect(draft.buildPayload(question({ type: '填空题' }))).toEqual(['甲', '乙'])
  })

  it('简答题返回文本', () => {
    const draft = useAnswerDraft()
    draft.shortAns.value = '参考答案'
    expect(draft.buildPayload(question({ type: '简答题' }))).toBe('参考答案')
  })

  it('判断题返回所选项', () => {
    const draft = useAnswerDraft()
    draft.picked.value = '正确'
    expect(draft.buildPayload(question({ type: '判断题' }))).toBe('正确')
  })

  it('拖拽题把 right→left 的交互状态反转为 left→right 的提交映射', () => {
    const draft = useAnswerDraft()
    draft.dragMap['右1'] = '左1'
    draft.dragMap['右2'] = '左2'
    expect(draft.buildPayload(question({ type: '拖拽题' }))).toEqual({ 左1: '右1', 左2: '右2' })
  })
})

describe('useAnswerDraft.applyAnswer', () => {
  it('题目为空时安全重置', () => {
    const draft = useAnswerDraft()
    draft.picked.value = 'A'
    draft.applyAnswer(undefined, undefined)
    expect(draft.picked.value).toBe('')
    expect(draft.blanks.value).toEqual([''])
  })

  it('填空题空位数按题干中连续下划线数量决定', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '填空题', question: '甲__乙__丙' }), null)
    expect(draft.blanks.value).toEqual(['', ''])
  })

  it('填空题无下划线标记时按 1 空处理', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '填空题', question: '没有标记' }), null)
    expect(draft.blanks.value).toEqual([''])
  })

  it('多选题字符串还原为字母数组', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '多选题' }), 'AC')
    expect(draft.multiPicked.value).toEqual(['A', 'C'])
  })

  it('简答题字符串写入 shortAns，不污染单选字段', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '简答题' }), '一段参考答案')
    expect(draft.shortAns.value).toBe('一段参考答案')
    expect(draft.picked.value).toBe('')
  })

  it('拖拽题映射还原为 right→left 的交互状态', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '拖拽题', left_items: ['左1'], right_items: ['右1'] }), { 左1: '右1' })
    expect(draft.dragMap).toEqual({ 右1: '左1' })
  })

  it('随机打乱左侧题项只在练习页开启，考试页保持卷面顺序', () => {
    const q = question({ type: '拖拽题', left_items: ['甲', '乙', '丙'], right_items: ['1'] })
    const exam = useAnswerDraft()
    exam.applyAnswer(q, null, { randomizeLeft: false })
    expect(exam.shuffledLeft.value).toEqual(['甲', '乙', '丙'])

    const practice = useAnswerDraft()
    practice.applyAnswer(q, null, { randomizeLeft: true })
    // 打乱后仍是同一组元素（顺序随机，故只校验集合相等）
    expect([...practice.shuffledLeft.value].sort()).toEqual([...['甲', '乙', '丙']].sort())
  })

  it('切换题目时先清空上一题的作答', () => {
    const draft = useAnswerDraft()
    draft.picked.value = 'A'
    draft.multiPicked.value = ['B']
    draft.shortAns.value = '残留'
    draft.dragMap['右'] = '左'
    draft.applyAnswer(question({ type: '单选题' }), null)
    expect(draft.picked.value).toBe('')
    expect(draft.multiPicked.value).toEqual([])
    expect(draft.shortAns.value).toBe('')
    expect(draft.dragMap).toEqual({})
  })
})

describe('useAnswerDraft.hasAnswer', () => {
  it('空值/空白/空集合都算未作答', () => {
    const { hasAnswer } = useAnswerDraft()
    expect(hasAnswer(null)).toBe(false)
    expect(hasAnswer(undefined)).toBe(false)
    expect(hasAnswer('')).toBe(false)
    expect(hasAnswer('   ')).toBe(false)
    expect(hasAnswer([])).toBe(false)
    expect(hasAnswer([''])).toBe(false)
    expect(hasAnswer({})).toBe(false)
  })

  it('有内容算已作答', () => {
    const { hasAnswer } = useAnswerDraft()
    expect(hasAnswer('A')).toBe(true)
    expect(hasAnswer(['甲'])).toBe(true)
    expect(hasAnswer({ 左: '右' })).toBe(true)
  })

  it('多选全取消后不算已作答', () => {
    const draft = useAnswerDraft()
    draft.multiPicked.value = ['A']
    draft.toggleMulti('A')
    expect(draft.hasAnswer(draft.buildPayload(question({ type: '多选题' })))).toBe(false)
  })
})

describe('useAnswerDraft 交互辅助', () => {
  it('toggleMulti 追加与移除', () => {
    const draft = useAnswerDraft()
    draft.toggleMulti('A')
    draft.toggleMulti('B')
    expect(draft.multiPicked.value).toEqual(['A', 'B'])
    draft.toggleMulti('A')
    expect(draft.multiPicked.value).toEqual(['B'])
  })

  it('updateBlank 按下标写入', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '填空题', question: 'a__b' }), null)
    draft.updateBlank(1, '乙')
    expect(draft.blanks.value).toEqual(['', '乙'])
  })

  it('pickSource 放入第一个空位；dropOn 会解除该题项原先的占用', () => {
    const q = question({ type: '拖拽题', left_items: ['甲'], right_items: ['右1', '右2'] })
    const draft = useAnswerDraft()
    draft.applyAnswer(q, null)
    draft.pickSource(q, '甲')
    expect(draft.dragMap).toEqual({ 右1: '甲' })

    draft.startDrag('甲')
    draft.dropOn('右2')
    expect(draft.dragMap).toEqual({ 右2: '甲' })
    expect(draft.draggingItem.value).toBe('')
  })

  it('对已放置的题项再次 pickSource 不会占用第二个空位（否则 allDragFilled 误判为已填满）', () => {
    const q = question({ type: '拖拽题', left_items: ['甲', '乙'], right_items: ['右1', '右2'] })
    const draft = useAnswerDraft()
    draft.applyAnswer(q, null)
    draft.pickSource(q, '甲')
    draft.pickSource(q, '甲') // 连点两次：旧实现会写成 { 右1: '甲', 右2: '甲' }

    expect(draft.dragMap).toEqual({ 右1: '甲' })
    // 乙尚未放置，拖拽题不应被视为填满（提交按钮的 disabled 依据）
    expect(draft.allDragFilled(q)).toBe(false)
    // 反转后仍只有一条映射，不会静默丢弃
    expect(draft.buildPayload(q)).toEqual({ 甲: '右1' })
  })

  it('applyAnswer 还原二维填空答案时取每空首个等价答案，而不是写成空串', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '填空题', question: '甲__乙__' }), [['甲', '甲选项'], ['乙']])
    expect(draft.blanks.value).toEqual(['甲', '乙'])
  })

  it('unassign 移除指定容器上的答案', () => {
    const draft = useAnswerDraft()
    draft.applyAnswer(question({ type: '拖拽题', left_items: ['甲'], right_items: ['右1'] }), { 甲: '右1' })
    draft.unassign('右1')
    expect(draft.dragMap).toEqual({})
  })

  it('allDragFilled 要求每个右侧容器都已填', () => {
    const q = question({ type: '拖拽题', left_items: ['甲', '乙'], right_items: ['右1', '右2'] })
    const draft = useAnswerDraft()
    draft.applyAnswer(q, { 甲: '右1' })
    expect(draft.allDragFilled(q)).toBe(false)
    draft.applyAnswer(q, { 甲: '右1', 乙: '右2' })
    expect(draft.allDragFilled(q)).toBe(true)
  })
})

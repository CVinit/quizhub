import { api } from '@/api/http'

/**
 * 题目内容字段是多态的（随题型变化），此处用具名联合别名代替 any，
 * 至少约束住顶层形态：选择题 options 为字符串数组、填空题 answer 为二维数组、
 * 拖拽题 answer 为映射。页面在按题型分支后即可安全收敛到具体类型。
 */
export type QuestionOptions = string[] | null
export type QuestionPairItems = string[] | null
/**
 * 答案：随题型变化的多态联合。
 * - 单选/多选/判断/简答：字符串
 * - 填空：每空可有多个等价答案，故为 string[][]；前端编辑态用扁平的 string[]
 *   （一空一个输入框），提交时由后端按 string[] 或 string[][] 均可解析。
 * - 拖拽：left→right 映射
 */
export type QuestionAnswer = string | string[] | string[][] | Record<string, string> | null

export interface Question {
  id: number
  type: string
  question: string
  options: QuestionOptions
  left_items: QuestionPairItems
  right_items: QuestionPairItems
  analysis: string
  difficulty: number
  tags: string[] | null
  score: number
  /** 本人是否已标记（后端按当前用户附带） */
  marked?: boolean
  /** 本人标记备注（后端按当前用户附带） */
  marked_note?: string
}

export interface BankBrief {
  id: number
  name: string
  count: number
}

export interface PracticeModes {
  total: number
  practiced: number
  wrong: number
  marked: number
  type_dist: Record<string, number>
  banks: BankBrief[]
}

export const practiceApi = {
  modes: (bank_id?: number) =>
    api.get<PracticeModes>('/records/practice/modes', { params: bank_id ? { bank_id } : {} }),
  start: (mode: string, type?: string, limit?: number, bank_id?: number) =>
    api.post<Question[]>('/records/practice/start', { mode, type, limit, bank_id }),
  answer: (question_id: number, answer: QuestionAnswer, mode: string) =>
    api.post<{
      is_correct: boolean | null
      correct_answer: QuestionAnswer
      analysis: string
      reference_answer: string | null
    }>('/records/practice/answer', { question_id, answer, mode }),
  toggleMark: (qid: number, marked: boolean, note?: string) =>
    api.post(`/records/questions/${qid}/toggle-mark`, { marked, note: note || '' }),
  shortEval: (qid: number, mastered: boolean) => api.post(`/records/questions/${qid}/short-eval`, { mastered }),
}

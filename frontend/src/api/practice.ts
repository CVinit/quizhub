import { api } from '@/api/http'

export interface Question {
  id: number
  type: string
  question: string
  options: any
  left_items: any
  right_items: any
  analysis: string
  difficulty: number
  tags: string[] | null
  score: number
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
  modes: (bank_id?: number) => api.get<PracticeModes>(
    '/records/practice/modes', { params: bank_id ? { bank_id } : {} },
  ),
  start: (mode: string, type?: string, limit?: number, bank_id?: number) =>
    api.post<Question[]>('/records/practice/start', { mode, type, limit, bank_id }),
  answer: (question_id: number, answer: any, mode: string) =>
    api.post<{ is_correct: boolean | null; correct_answer: any; analysis: string; reference_answer: any | null }>('/records/practice/answer', { question_id, answer, mode }),
  progress: () => api.get<{ total: number; practiced: number }>('/records/practice/progress'),
  toggleMark: (qid: number, marked: boolean, note?: string) =>
    api.post(`/records/questions/${qid}/toggle-mark`, { marked, note: note || '' }),
  shortEval: (qid: number, mastered: boolean) =>
    api.post(`/records/questions/${qid}/short-eval`, { mastered }),
}

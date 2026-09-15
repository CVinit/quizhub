import { api } from '@/api/http'

export interface ExamBrief {
  id: number
  name: string
  type: string
  status: string
  start_at: string | null
  end_at: string | null
  duration_min: number
  pass_score: number
  state: string
  attempts: number
  max_attempts: number
  total_questions: number
  // 以下字段仅管理端列表返回（供编辑弹窗回填），用户端 /exams/available 不含
  rules?: { type_quota?: Record<string, number>; bank_ids?: number[]; group_ids?: number[]; tags?: string[] }
  paper_template_id?: number | null
  manual_questions?: number[] | null
  group_ids?: number[]
  need_review?: boolean
  show_score_immediately?: boolean
  show_analysis?: boolean
}

export interface ExamSession {
  session_id: number
  version: number
  answers: Record<string, any>
  remaining_sec: number | null
  duration_min: number
  started_at: string
  exam_name: string
  questions: ExamQuestion[]
}

export interface ExamQuestion {
  seq: number
  id: number
  type: string
  question: string
  options: any
  left_items: any
  right_items: any
  score: number
}

export const examApi = {
  available: () => api.get<ExamBrief[]>('/exams/available'),
  startMock: () => api.post<ExamSession>('/exams/mock/start'),
  start: (examId: number) => api.post<ExamSession>(`/exams/${examId}/start`),
  answer: (sid: number, question_id: number, answer: any, version: number) =>
    api.post<{ version: number }>(`/exams/session/${sid}/answer`, { question_id, answer, version }),
  submit: (sid: number) => api.post(`/exams/session/${sid}/submit`),
  result: (sid: number) => api.get(`/exams/session/${sid}/result`),
  sessionDetail: (sid: number) => api.get<ExamSession & { finished?: boolean; status?: string }>(`/exams/session/${sid}/detail`),
  // admin
  listTemplates: () => api.get('/admin/exam-templates'),
  previewPaper: (config: any) => api.post('/admin/exam-templates/preview-paper', config),
  createTemplate: (data: any) => api.post('/admin/exam-templates', data),
  deleteTemplate: (id: number) => api.delete(`/admin/exam-templates/${id}`),
  // status 省略时返回全部状态（含已归档）
  listExams: (status?: string) =>
    api.get<ExamBrief[]>('/admin/exams', { params: status ? { status } : {} }),
  // outcome: passed/failed/pending/published，省略为全部
  listResults: (exam_id?: number, outcome?: string) =>
    api.get('/admin/exam-results', { params: { exam_id, ...(outcome ? { outcome } : {}) } }),
  createExam: (data: any) => api.post('/admin/exams', data),
  updateExam: (id: number, data: any) => api.put(`/admin/exams/${id}`, data),
  deleteExam: (id: number) => api.delete(`/admin/exams/${id}`),
  // 归档：对用户隐藏但保留成绩（用于已有作答记录的测试考试）
  archiveExam: (id: number) => api.post(`/admin/exams/${id}/archive`),
  unarchiveExam: (id: number) => api.post(`/admin/exams/${id}/unarchive`),
  publishExam: (id: number) => api.post(`/admin/exams/${id}/publish`),
  getMockConfig: () => api.get('/admin/mock-config'),
  saveMockConfig: (config: any) => api.put('/admin/mock-config', { config }),
  // review
  // verdict 省略时默认只返回待复核；done=全部已复核
  listPendingReviews: (verdict?: string) =>
    api.get('/admin/review/pending', { params: verdict ? { verdict } : {} }),
  doReview: (id: number, verdict: string, partial_score?: number) =>
    api.post(`/admin/review/${id}`, { verdict, partial_score }),
  publishResults: (examId: number) => api.post(`/admin/exams/${examId}/publish-results`),
}

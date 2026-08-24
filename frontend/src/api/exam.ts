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
  listExams: () => api.get<ExamBrief[]>('/admin/exams'),
  listResults: (exam_id?: number) => api.get('/admin/exam-results', { params: { exam_id } }),
  createExam: (data: any) => api.post('/admin/exams', data),
  updateExam: (id: number, data: any) => api.put(`/admin/exams/${id}`, data),
  publishExam: (id: number) => api.post(`/admin/exams/${id}/publish`),
  getMockConfig: () => api.get('/admin/mock-config'),
  saveMockConfig: (config: any) => api.put('/admin/mock-config', { config }),
  // review
  listPendingReviews: () => api.get('/admin/review/pending'),
  doReview: (id: number, verdict: string, partial_score?: number) =>
    api.post(`/admin/review/${id}`, { verdict, partial_score }),
  publishResults: (examId: number) => api.post(`/admin/exams/${examId}/publish-results`),
}

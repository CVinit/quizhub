import { api, skipErrorToast } from '@/api/http'
import type { QuestionAnswer, QuestionOptions, QuestionPairItems } from '@/api/practice'

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
  /** 题目 id（字符串）→ { answer } */
  answers: Record<string, { answer: QuestionAnswer }>
  remaining_sec: number | null
  duration_min: number
  started_at: string
  exam_name: string
  questions: ExamQuestion[]
}

/**
 * 会话详情：`start`/`startMock` 幂等返回进行中会话，`sessionDetail` 在会话已结束时
 * 会带上 `finished/status`，故统一用该类型，调用方无需再 `as any`。
 */
export interface ExamSessionDetail extends ExamSession {
  finished?: boolean
  status?: string
}

export interface ExamQuestion {
  seq: number
  id: number
  type: string
  question: string
  options: QuestionOptions
  left_items: QuestionPairItems
  right_items: QuestionPairItems
  score: number
}

export interface ExamTemplate {
  id: number
  name: string
  mode: string
  question_count: number
  config: PaperConfig
  group_ids?: number[]
  created_at?: string
}

/** 组卷配置（模板与模拟考试设置共用）。 */
export interface PaperConfig {
  type_quota?: Record<string, number>
  bank_ids?: number[]
  group_ids?: number[]
  tags?: string[]
  difficulty_dist?: Record<string, number>
  /** 仅前端表单使用：难度比例的 JSON 文本，提交前解析为 difficulty_dist 并删除 */
  difficulty_dist_raw?: string
  allow_duplicate?: boolean
  max_questions?: number
  order_mode?: string
}

/** 组卷预览结果。 */
export interface PaperPreview {
  count: number
  total_score: number
  questions: { id: number; type: string; question: string; difficulty: number; score: number }[]
}

/** 模拟考试可选题库（仅含开放练习的题库）。 */
export interface MockBank {
  id: number
  name: string
  question_count: number
  /** 各题型题量，用于题型比例设置时提示可用余量 */
  type_stats: Record<string, number>
}

/** 模拟考试可选项（题库列表 + 题量档位 + 上限）。 */
export interface MockOptions {
  banks: MockBank[]
  total_questions: number
  size_presets: number[]
  default_size: number
  max_questions: number
}

/** 模拟考试组卷设置（题库/题量/题型比例均由用户开考前指定）。 */
export interface MockPaperSpec {
  bank_ids?: number[] | null
  size?: number | null
  type_quota?: Record<string, number> | null
  allocation?: 'auto' | 'manual'
  objective_only?: boolean
  show_analysis?: boolean
}

/** 模拟考试组卷预览结果。 */
export interface MockPreview {
  count: number
  total_score: number
  type_dist: Record<string, number>
  avail_by_type: Record<string, number>
  avail_total: number
  effective_size: number
  /** 范围内题量不足、题量被下调时为 true */
  size_downgraded: boolean
}

/** 考试记录（管理端）。 */
export interface ExamResultRow {
  id: number
  exam_name: string
  user_email: string
  user_name?: string
  score: number
  total_score: number
  correct_count: number
  total_count: number
  passed: boolean
  published: boolean
  submitted_at?: string
}

/** 简答复核条目。 */
export interface ReviewItem {
  id: number
  exam_name: string
  user: string
  question_id: number
  question: string
  user_answer: string
  reference_answer: string
  /** 该题在本次考试中的分值：部分得分上限必须按它校验 */
  score: number
  verdict?: string | null
  partial_score?: number | null
  reviewed_at?: string | null
}

/** 考试创建/更新载荷（后端 ExamUpdateIn 为 extra=forbid，type 仅创建时携带）。 */
export interface ExamWritePayload {
  name: string
  rules: PaperConfig
  paper_template_id: number | null
  group_ids: number[]
  start_at: string | null
  end_at: string | null
  duration_min: number
  pass_score: number
  max_attempts: number
  need_review: boolean
  show_score_immediately: boolean
  show_analysis: boolean
  /** 仅创建时携带，创建后不可变 */
  type?: string
  /**
   * 仅更新时使用：改动了组卷来源（rules/paper_template_id/manual_questions）且该考试
   * 已有作答时，必须传 true 才会作废旧作答并重新固化卷面；否则后端返回 409 影响面提示。
   */
  confirm_reset?: boolean
}

/** 交卷响应：含简答的考试需复核，故成绩字段可能缺省。 */
export interface ExamSubmitResult {
  need_review?: boolean
  score?: number
  total_score?: number
  passed?: boolean
  correct_count?: number
  total_count?: number
}

export const examApi = {
  available: () => api.get<ExamBrief[]>('/exams/available'),
  // 模拟考试：完全用户自助，题库/题量/题型比例由开考前的设置对话框传入
  mockOptions: () => api.get<MockOptions>('/exams/mock/banks'),
  // /mock/preview 的入参是 MockPaperIn（extra="forbid"，无 show_analysis），
  // show_analysis 仅 /mock/start 接受；此处收窄类型，避免把该字段发到预览接口导致 422。
  previewMock: (spec: Omit<MockPaperSpec, 'show_analysis'>) => api.post<MockPreview>('/exams/mock/preview', spec),
  startMock: (spec: MockPaperSpec) => api.post<ExamSessionDetail>('/exams/mock/start', spec),
  start: (examId: number) => api.post<ExamSessionDetail>(`/exams/${examId}/start`),
  answer: (
    sid: number,
    question_id: number,
    answer: QuestionAnswer,
    version: number,
    opts?: { quiet?: boolean },
  ) =>
    api.post<{ version: number }>(
      `/exams/session/${sid}/answer`,
      { question_id, answer, version },
      opts?.quiet ? skipErrorToast() : undefined,
    ),
  submit: (sid: number) => api.post<ExamSubmitResult>(`/exams/session/${sid}/submit`),
  sessionDetail: (sid: number) => api.get<ExamSessionDetail>(`/exams/session/${sid}/detail`),
  // admin
  listTemplates: () => api.get<ExamTemplate[]>('/admin/exam-templates'),
  // 后端 PaperPreviewIn 为 extra="forbid"，且不含 allow_duplicate（那是 PaperConfig 的落库字段），
  // 也不接受仅前端表单使用的 difficulty_dist_raw。统一在这里剔除，否则预览接口一律 422。
  previewPaper: (config: PaperConfig) => {
    const payload: PaperConfig = { ...config }
    delete payload.allow_duplicate
    delete payload.difficulty_dist_raw
    return api.post<PaperPreview>('/admin/exam-templates/preview-paper', payload)
  },
  createTemplate: (data: { name: string; mode: string; config: PaperConfig; group_ids: number[] }) =>
    api.post('/admin/exam-templates', data),
  deleteTemplate: (id: number) => api.delete(`/admin/exam-templates/${id}`),
  // status 省略时返回全部状态（含已归档）
  listExams: (status?: string) => api.get<ExamBrief[]>('/admin/exams', { params: status ? { status } : {} }),
  // outcome: passed/failed/pending/published，省略为全部
  listResults: (exam_id?: number, outcome?: string) =>
    api.get<ExamResultRow[]>('/admin/exam-results', {
      params: { exam_id, ...(outcome ? { outcome } : {}) },
    }),
  createExam: (data: ExamWritePayload) => api.post('/admin/exams', data),
  updateExam: (id: number, data: ExamWritePayload) => api.put(`/admin/exams/${id}`, data),
  deleteExam: (id: number) => api.delete(`/admin/exams/${id}`),
  // 归档：对用户隐藏但保留成绩（用于已有作答记录的测试考试）
  archiveExam: (id: number) => api.post(`/admin/exams/${id}/archive`),
  unarchiveExam: (id: number) => api.post(`/admin/exams/${id}/unarchive`),
  publishExam: (id: number) => api.post(`/admin/exams/${id}/publish`),
  // review
  // verdict 省略时默认只返回待复核；done=全部已复核
  listPendingReviews: (verdict?: string) =>
    api.get<ReviewItem[]>('/admin/review/pending', { params: verdict ? { verdict } : {} }),
  doReview: (id: number, verdict: string, partial_score?: number) =>
    api.post(`/admin/review/${id}`, { verdict, partial_score }),
  publishResults: (examId: number) => api.post(`/admin/exams/${examId}/publish-results`),
}

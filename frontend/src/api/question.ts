import { api } from '@/api/http'

export interface QuestionBank {
  id: number
  name: string
  group_id: number | null
  question_count?: number
}

export interface QuestionItem {
  id: number
  bank_id: number | null
  type: string
  question: string
  options: any
  left_items: any
  right_items: any
  answer: any
  analysis: string
  difficulty: number
  tags: string[] | null
  score: number
  group_id: number | null
}

export const questionApi = {
  listBanks: () => api.get<QuestionBank[]>('/admin/question-banks'),
  createBank: (name: string, group_id?: number | null) =>
    api.post('/admin/question-banks', { name, group_id }),
  list: (params: Record<string, any>) =>
    api.get<{ total: number; page: number; page_size: number; items: QuestionItem[] }>('/admin/questions', { params }),
  create: (data: Partial<QuestionItem>) => api.post('/admin/questions', data),
  update: (id: number, data: Partial<QuestionItem>) => api.put(`/admin/questions/${id}`, data),
  remove: (id: number) => api.delete(`/admin/questions/${id}`),
}

export const uploadApi = {
  // 模板下载需带鉴权头（window.open 无法携带 Authorization），故用 axios 取 Blob 再触发保存
  downloadTemplate: async () => {
    const blob = await api.get<Blob>('/admin/upload/template', { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '题库导入模板.xlsx'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
  preview: (file: File, group_id?: number | null, bank_id?: number | null, bank_name?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    if (group_id) fd.append('group_id', String(group_id))
    if (bank_id) fd.append('bank_id', String(bank_id))
    if (bank_name) fd.append('bank_name', bank_name)
    return api.post<{
      rows: any[]
      total: number
      valid_count: number
      type_dist: Record<string, number>
      errors: any[]
      confirm_token: string
    }>('/admin/upload/preview', fd)
  },
  doImport: (confirm_token: string) => {
    const fd = new FormData()
    fd.append('confirm_token', confirm_token)
    return api.post<{ success: number; failed: number }>('/admin/upload/import', fd)
  },
}

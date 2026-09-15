import { api } from '@/api/http'

export interface UserItem {
  id: number
  email: string
  name: string
  role: 'user' | 'dept_admin' | 'super_admin'
  status: 'active' | 'pending' | 'disabled'
  email_verified: boolean
  dept_group_id: number | null
  groups: number[]
}

export interface UserPage {
  total: number
  page: number
  page_size: number
  items: UserItem[]
}

export interface UserCreatePayload {
  email: string
  name?: string
  role?: 'user' | 'dept_admin' | 'super_admin'
  password: string
  status?: 'active' | 'pending' | 'disabled'
  group_ids?: number[]
}

export type UserCreateResult = UserItem

export interface UserImportPreviewRow {
  row_index: number
  email: string
  name: string
  role: string
  status: string
  group_ids: number[]
  valid: boolean
  error: string
}

export interface UserImportPreview {
  rows: UserImportPreviewRow[]
  total: number
  valid_count: number
  errors: { row: number; email: string; error: string }[]
  confirm_token: string
}

export interface UserImportResult {
  success: number
  failed: number
  errors: { row: number; email: string; error: string }[]
}

export const userApi = {
  list: (params: Record<string, any>) => api.get<UserPage>('/admin/users', { params }),
  create: (data: UserCreatePayload) => api.post<UserCreateResult>('/admin/users', data),
  update: (id: number, data: Partial<UserItem>) => api.put(`/admin/users/${id}`, data),
  approve: (id: number) => api.post(`/admin/users/${id}/approve`),
  disable: (id: number) => api.post(`/admin/users/${id}/disable`),
  enable: (id: number) => api.post(`/admin/users/${id}/enable`),
  remove: (id: number) => api.delete(`/admin/users/${id}`),
  resetPassword: (id: number, new_password: string) =>
    api.post<{ success: boolean }>(`/admin/users/${id}/reset-password`, { new_password }),
  assignGroups: (id: number, group_ids: number[]) =>
    api.post(`/admin/users/${id}/groups`, { group_ids }),
  // 批量导入用户
  downloadImportTemplate: async () => {
    const blob = await api.get<Blob>('/admin/users/import/template', { responseType: 'blob' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '用户导入模板.xlsx'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  },
  importPreview: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<UserImportPreview>('/admin/users/import/preview', fd)
  },
  doImport: (confirm_token: string) => {
    const fd = new FormData()
    fd.append('confirm_token', confirm_token)
    return api.post<UserImportResult>('/admin/users/import', fd)
  },
}

import { api } from '@/api/http'

export interface SettingField {
  key: string
  label: string
  encrypted: boolean
  value_type: 'text' | 'number' | 'bool'
}

export interface SettingCategory {
  key: string
  label: string
  fields: SettingField[]
}

export interface SettingItem {
  key: string
  value: string
  encrypted?: boolean
  category?: string
}

export const systemApi = {
  categories: () => api.get<SettingCategory[]>('/system/categories'),
  listSettings: (category?: string) => api.get<SettingItem[]>('/system/settings', { params: { category } }),
  updateSettings: (category: string, updates: Record<string, string>) =>
    api.put('/system/settings', { category, updates }),
  smtpTest: (to_email: string) => api.post<{ message?: string }>('/system/smtp/test', { to_email }),
  // Logo 上传：返回 { url }（公开可读路径）
  uploadLogo: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post<{ url: string }>('/system/logo', fd)
  },
}

import { api } from '@/api/http'
import type { GroupNode } from '@/api/group'

export const authApi = {
  captcha: () => api.get<{ captcha_id: string; image: string }>('/auth/captcha'),
  sendCode: (email: string, captcha_id: string, captcha_code: string) =>
    api.post('/auth/send-code', { email, captcha_id, captcha_code }),
  register: (email: string, password: string, name: string, code: string, group_ids: number[] = []) =>
    api.post('/auth/register', { email, password, name, code, group_ids }),
  registerGroups: () => api.get<{ groups: GroupNode[]; required: boolean }>('/auth/register-groups'),
  verify: (email: string, code: string) => api.post('/auth/verify', { email, code }),
  resend: (email: string) => api.post('/auth/resend-verification', { email }),
  changePassword: (old_password: string, new_password: string) =>
    // 改密后旧 token 立即失效，响应里带回新签发的 token（见后端 ChangePasswordOut）
    api.post<{ access_token: string; token_type: string }>('/auth/change-password', { old_password, new_password }),
}

import { defineStore } from 'pinia'
import http from '@/api/http'

export interface UserInfo {
  id: number
  email: string
  name: string
  role: 'user' | 'dept_admin' | 'super_admin'
  status: 'active' | 'pending' | 'disabled'
  email_verified: boolean
}

interface LoginResp {
  access_token: string
  token_type: string
  user: UserInfo
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('training_token') || '',
    user: JSON.parse(localStorage.getItem('training_user') || 'null') as UserInfo | null,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'dept_admin' || s.user?.role === 'super_admin',
    isSuper: (s) => s.user?.role === 'super_admin',
  },
  actions: {
    async login(email: string, password: string) {
      const data = await http.post<any, LoginResp>('/auth/login', { username: email, password })
      this.token = data.access_token
      this.user = data.user
      localStorage.setItem('training_token', this.token)
      localStorage.setItem('training_user', JSON.stringify(this.user))
    },
    async fetchMe() {
      const u = await http.get<any, UserInfo>('/auth/me')
      this.user = u
      localStorage.setItem('training_user', JSON.stringify(u))
    },
    clear() {
      this.token = ''
      this.user = null
      localStorage.removeItem('training_token')
      localStorage.removeItem('training_user')
    },
    async logout() {
      this.clear()
    },
  },
})

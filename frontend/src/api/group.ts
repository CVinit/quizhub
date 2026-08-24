import { api } from '@/api/http'

export interface GroupNode {
  id: number
  name: string
  type: string
  parent_id: number | null
  sort: number
  children: GroupNode[]
}

export const groupApi = {
  tree: () => api.get<GroupNode[]>('/admin/groups'),
  create: (data: { name: string; type: string; parent_id?: number | null; sort?: number }) =>
    api.post('/admin/groups', data),
  update: (id: number, data: Partial<{ name: string; type: string; parent_id: number | null; sort: number }>) =>
    api.put(`/admin/groups/${id}`, data),
  remove: (id: number) => api.delete(`/admin/groups/${id}`),
}

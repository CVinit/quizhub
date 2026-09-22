/**
 * 用户域展示常量。
 *
 * 用户列表、新增/编辑弹窗、批量导入预览都展示同一套角色/状态文案与配色，
 * 集中定义避免各页面手抄后出现「同一个状态两种叫法」。
 */
import type { TagType } from '@/constants/ui'
import type { UserItem } from '@/api/user'

export type UserRole = UserItem['role']
export type UserStatus = UserItem['status']

export const USER_ROLE_LABELS: Record<string, string> = {
  user: '普通用户',
  dept_admin: '部门管理员',
  super_admin: '超级管理员',
}

export const USER_STATUS_LABELS: Record<string, string> = {
  pending: '待审批',
  active: '正常',
  disabled: '已禁用',
}

/** 状态标签配色（el-tag type）。 */
export const USER_STATUS_TAGS: Record<string, TagType> = {
  pending: 'warning',
  active: 'success',
  disabled: 'info',
}

export const roleLabel = (role: string) => USER_ROLE_LABELS[role] || role
export const statusLabel = (status: string) => USER_STATUS_LABELS[status] || status
export const statusTag = (status: string): TagType => USER_STATUS_TAGS[status] || 'info'

/** 下拉选项（筛选/编辑/新增共用），避免同一份 role/status 选项在多处手抄后漏改。 */
export const USER_ROLE_OPTIONS = Object.entries(USER_ROLE_LABELS).map(([value, label]) => ({ value, label }))
export const USER_STATUS_OPTIONS = Object.entries(USER_STATUS_LABELS).map(([value, label]) => ({ value, label }))

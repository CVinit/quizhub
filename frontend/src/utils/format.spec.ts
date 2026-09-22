import { describe, expect, it } from 'vitest'
import { bankLabel, formatDateTime } from '@/utils/format'

describe('formatDateTime', () => {
  it('空值返回空串', () => {
    expect(formatDateTime(null)).toBe('')
    expect(formatDateTime(undefined)).toBe('')
    expect(formatDateTime('')).toBe('')
  })

  it('纯日期按本地时间解析，不因 UTC 解析整体偏移', () => {
    const expected = new Date('2024-01-02T00:00:00').toLocaleString('zh-CN', { hour12: false })
    expect(formatDateTime('2024-01-02')).toBe(expected)
  })

  it('无偏移的日期时间按本地时间解析', () => {
    const expected = new Date('2024-01-02T03:04:05').toLocaleString('zh-CN', { hour12: false })
    expect(formatDateTime('2024-01-02T03:04:05')).toBe(expected)
  })

  it('带偏移的日期时间按绝对时刻解析', () => {
    const expected = new Date('2024-01-02T03:04:05Z').toLocaleString('zh-CN', { hour12: false })
    expect(formatDateTime('2024-01-02T03:04:05Z')).toBe(expected)
  })

  it('无法解析时原样返回，避免显示 Invalid Date', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })
})

describe('bankLabel', () => {
  it('有题量时附带题量', () => {
    expect(bankLabel({ name: '题库A', question_count: 12 })).toBe('题库A（12 题）')
  })

  it('题量为 0 也附带题量（0 是有效值，不等于"未返回"）', () => {
    expect(bankLabel({ name: '空题库', question_count: 0 })).toBe('空题库（0 题）')
  })

  it('question_count 缺失或为 null 时只显示名称，不渲染 undefined', () => {
    expect(bankLabel({ name: '题库A' })).toBe('题库A')
    expect(bankLabel({ name: '题库A', question_count: null })).toBe('题库A')
  })
})

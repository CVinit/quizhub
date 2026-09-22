import { describe, expect, it } from 'vitest'
import { shuffle } from '@/utils/array'

describe('shuffle', () => {
  it('返回新数组，不改动入参', () => {
    const input = [1, 2, 3, 4, 5]
    const out = shuffle(input)
    expect(out).not.toBe(input)
    expect(input).toEqual([1, 2, 3, 4, 5])
  })

  it('是排列：元素不丢失、不重复', () => {
    const input = Array.from({ length: 50 }, (_, i) => i)
    const out = shuffle(input)
    expect(out).toHaveLength(50)
    expect([...out].sort((a, b) => a - b)).toEqual(input)
    expect(new Set(out).size).toBe(50)
  })

  it('空数组与单元素数组安全', () => {
    expect(shuffle([])).toEqual([])
    expect(shuffle([1])).toEqual([1])
  })

  it('支持只读数组入参', () => {
    const input = ['a', 'b', 'c'] as const
    expect([...shuffle(input)].sort()).toEqual(['a', 'b', 'c'])
  })
})

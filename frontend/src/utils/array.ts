/**
 * 通用工具函数。
 */

/**
 * Fisher–Yates 洗牌，返回新数组（不改动入参）。
 *
 * 不用 `arr.sort(() => Math.random() - 0.5)`：该写法依赖比较器的“随机性”，
 * 实际分布有偏（部分排列几乎不会出现），对错题/选项顺序敏感的场景会造成
 * 抽题不均。此处为无偏洗牌。
 */
export function shuffle<T>(items: readonly T[]): T[] {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

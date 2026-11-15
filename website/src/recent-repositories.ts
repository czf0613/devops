import { ref } from 'vue'

// Keep only stable numeric IDs; names and visibility always come from the server.
export const recentRepositoryRevision = ref(0)
const key = (userId: number) => `devops.recent-repositories.${userId}`
export function recentRepositoryIds(userId: number): number[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key(userId)) || '[]')
    return Array.isArray(stored)
      ? [...new Set(stored.filter((value): value is number => Number.isSafeInteger(value) && value > 0))].slice(0, 20)
      : []
  } catch { return [] }
}
export function rememberRepository(userId: number | undefined, repositoryId: number) {
  if (!userId || !Number.isSafeInteger(repositoryId) || repositoryId <= 0) return
  try {
    localStorage.setItem(key(userId), JSON.stringify(
      [repositoryId, ...recentRepositoryIds(userId).filter(id => id !== repositoryId)].slice(0, 20),
    ))
    recentRepositoryRevision.value++
  } catch { /* History is optional when storage is unavailable. */ }
}

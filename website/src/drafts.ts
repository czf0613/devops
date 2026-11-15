import { computed, ref, watch, type Ref } from 'vue'
import { auth } from './auth'

const prefix = 'devops:discussion-draft:v1:'
const lifetime = 30 * 24 * 60 * 60 * 1000
type Fields = Record<string, Ref>

// Only form content lives here. Authentication continues to use the server cookie.
export function useDraft(scope: () => string, fields: Fields, enabled = () => true) {
  const key = computed(() => auth.user ? `${prefix}${auth.user.id}:${scope()}` : '')
  const saved = ref(false), restored = ref(false), failure = ref('')
  let initialized = false, baseline = '', ownerKey = ''
  const snapshot = () => Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]))
  const message = computed(() => failure.value || (saved.value
    ? restored.value ? '已恢复草稿 · 仅保存在此浏览器' : '草稿已暂存到此浏览器'
    : ''))
  function persist() {
    if (!initialized || !enabled() || !ownerKey || ownerKey !== key.value) return
    try {
      const value = snapshot(), text = JSON.stringify(value)
      if (text === baseline) {
        localStorage.removeItem(ownerKey)
        saved.value = restored.value = false
        return
      }
      localStorage.setItem(ownerKey, JSON.stringify({ savedAt: Date.now(), value }))
      saved.value = true
      failure.value = ''
    } catch {
      failure.value = '浏览器暂时无法保存草稿，请在离开前复制内容。'
    }
  }
  function restore() {
    baseline = JSON.stringify(snapshot())
    ownerKey = key.value
    initialized = false
    saved.value = restored.value = false
    if (ownerKey) {
      try {
        const raw = localStorage.getItem(ownerKey)
        if (raw) {
          const stored = JSON.parse(raw)
          const valid = stored.savedAt > Date.now() - lifetime && stored.savedAt <= Date.now() &&
            stored.value && Object.keys(stored.value).some(name => name in fields) && Object.keys(fields).every(name => {
              const value = stored.value[name], original = fields[name]!.value
              return value === undefined || (Array.isArray(original) ? Array.isArray(value)
                : original === null ? value === null || typeof value === 'object'
                  : typeof original === typeof value && (typeof value !== 'object' || value !== null))
            })
          if (valid) {
            for (const [name, field] of Object.entries(fields)) {
              if (stored.value[name] !== undefined) field.value = stored.value[name]
            }
            saved.value = restored.value = true
          } else localStorage.removeItem(ownerKey)
        }
      } catch {
        failure.value = '浏览器暂时无法读取草稿，请在离开前复制内容。'
      }
    }
    initialized = true
    return restored.value
  }
  function clear() {
    // Reset the baseline before the next watcher; a successful submission must not recreate it.
    baseline = JSON.stringify(snapshot())
    saved.value = restored.value = false
    try { if (ownerKey) localStorage.removeItem(ownerKey); failure.value = '' }
    catch { failure.value = '浏览器暂时无法清除已保存的草稿。' }
  }
  watch(() => JSON.stringify(snapshot()), persist, { flush: 'sync' })
  return { restore, clear, message, saved, restored }
}

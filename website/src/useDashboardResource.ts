import { onMounted, onUnmounted, shallowRef, ref, watch } from 'vue'
import { api, errorText } from './api'
import { auth } from './auth'

/** Independent section requests, also refreshed when the user returns to the window. */
export function useDashboardResource<T>(path: () => string, empty: () => T) {
  const data = shallowRef<T>(empty()), busy = ref(false), error = ref('')
  let sequence = 0
  async function load() {
    const current = ++sequence, userId = auth.user?.id
    data.value = empty()
    error.value = ''
    busy.value = Boolean(userId)
    if (!userId) return
    try {
      const result = await api<T>(path())
      if (current === sequence && userId === auth.user?.id) data.value = result
    } catch (cause) {
      if (current === sequence && userId === auth.user?.id) error.value = errorText(cause)
    } finally {
      if (current === sequence) busy.value = false
    }
  }
  watch([path, () => auth.user?.id], load, { immediate: true })
  onMounted(() => window.addEventListener('focus', load))
  onUnmounted(() => { sequence++; window.removeEventListener('focus', load) })
  return { data, busy, error, load }
}

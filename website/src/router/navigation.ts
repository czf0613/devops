import type { RouteLocationNormalized, RouterScrollBehavior } from 'vue-router'

// A PR's sections share one editor and polling lifecycle. Different records and
// new-item contexts must still get independent component instances and drafts.
export function repositoryPageKey(route: RouteLocationNormalized) {
  const { owner, name, number } = route.params
  const tab = route.meta.repositoryTab
  if (owner && name && number && (tab === 'issue' || tab === 'pr'))
    return JSON.stringify([owner, name, tab, number])
  if (route.path.endsWith('/new') || route.path.endsWith('/compare'))
    return route.path + JSON.stringify([route.query.parent, route.query.source])
  return route.path
}

export const scrollBehavior: RouterScrollBehavior = (to, from, saved) => {
  if (saved) return saved
  // Components resolve anchors after their asynchronous content is available.
  if (to.hash || repositoryPageKey(to) === repositoryPageKey(from)) return false
  return { top: 0 }
}

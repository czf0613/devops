<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })
const paths: Record<string, string> = {
  pull: 'M6 7v10 M4 5a2 2 0 1 0 4 0 2 2 0 0 0-4 0 M4 19a2 2 0 1 0 4 0 2 2 0 0 0-4 0 M16 19a2 2 0 1 0 4 0 2 2 0 0 0-4 0 M18 17V9a4 4 0 0 0-4-4h-2 m3-3-3 3 3 3',
  issue: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 8v5 M12 16h.01',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4',
  search: 'M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 M15 15l6 6',
  copy: 'M9 9h12v12H9z M15 9V3H3v12h6',
  sidebar: 'M3 4h18v16H3z M9 4v16',
  branch: 'M6 3v12a4 4 0 0 0 4 4h3 M6 7h6a5 5 0 0 0 5-5 M4 3a2 2 0 1 0 4 0 2 2 0 0 0-4 0 M15 19a2 2 0 1 0 4 0 2 2 0 0 0-4 0',
  folder: 'M3 6h7l2 2h9v12H3z M3 6V4h7l2 2h9v2',
  file: 'M5 3h9l5 5v13H5z M14 3v6h5',
  code: 'm8 6-6 6 6 6 m8-12 6 6-6 6 M14 3l-4 18',
  settings: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
  repo: 'M4 4h14a2 2 0 0 1 2 2v15H6a2 2 0 0 1-2-2V4z M4 17h16 M8 4v8l3-2 3 2V4',
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  user: 'M20 21v-2a7 7 0 0 0-14 0v2 M13 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',
  users:
    'M16 21v-2a6 6 0 0 0-12 0v2 M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M17 4a4 4 0 0 1 0 8 M21 21v-2a6 6 0 0 0-3-5',
  shield: 'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z M8 12l3 3 5-6',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  chevron: 'm6 9 6 6 6-6',
  check: 'm5 12 4 4L19 6',
  close: 'm6 6 12 12 M6 18 18 6',
  server: 'M4 3h16v7H4z M4 14h16v7H4z M7 6h.01 M7 17h.01',
  key: 'M15 11a5 5 0 1 0-5-5 M10 7 3 14v4h4v-3h3l4-4',
  mail: 'M3 5h18v14H3z m0 0 9 8 9-8',
  arrow: 'M5 12h14 m-5-5 5 5-5 5',
  logout: 'M9 3H4v18h5 M9 12h12 m-5-5 5 5-5 5',
}
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    class="app-icon"
  >
    <path :d="paths[name] || paths.grid" />
  </svg>
</template>

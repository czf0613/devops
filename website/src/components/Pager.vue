<script setup lang="ts">
import AppButton from './AppButton.vue'
defineProps<{ page: number; total: number; busy?: boolean; pageSize?: number }>()
defineEmits<{ change: [page: number] }>()
</script>
<template>
  <div class="pager">
    <span
      >共 {{ total }} 条 · 第 {{ page }} /
      {{ Math.max(1, Math.ceil(total / (pageSize ?? 20))) }} 页</span
    >
    <div class="actions">
      <AppButton
        variant="secondary"
        class="small"
        :disabled="page <= 1 || busy"
        @click="$emit('change', page - 1)"
      >
        上一页</AppButton
      ><AppButton
        variant="secondary"
        class="small"
        :disabled="page * (pageSize ?? 20) >= total || busy"
        @click="$emit('change', page + 1)"
      >
        下一页
      </AppButton>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ tone?: 'green' | 'blue' | 'red' }>()
</script>
<template>
  <span class="badge" :class="tone"><slot /></span>
</template>

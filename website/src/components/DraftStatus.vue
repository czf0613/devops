<script setup lang="ts">
defineProps<{ message: string }>()
</script>
<template><p v-if="message" class="draft-status" role="status">{{ message }}</p></template>

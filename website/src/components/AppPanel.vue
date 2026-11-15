<script setup lang="ts">
defineProps<{ title?: string; description?: string; eyebrow?: string }>()
</script>
<template>
  <section class="panel">
    <div v-if="title || $slots.actions" class="section-heading">
      <div>
        <p v-if="eyebrow" class="eyebrow">{{ eyebrow }}</p>
        <h2 v-if="title">{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </div>
      <slot name="actions" />
    </div>
    <slot />
  </section>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="panel">
    <p class="eyebrow">404</p>
    <h1>页面不存在</h1>
    <p>请检查访问地址，或返回工作台继续。</p>
    <RouterLink class="button" to="/">返回工作台</RouterLink>
  </section>
</template>

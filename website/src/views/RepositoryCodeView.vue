<script setup lang="ts">
import { computed } from 'vue'
import type { Repository } from '../repositories'
import RepositoryCode from '../components/RepositoryCode.vue'
const props = defineProps<{ repository: Repository }>()
const cloneUrl = computed(() => new URL(props.repository.clone_path, window.location.origin).href)
</script>
<template><RepositoryCode :repository="repository" :clone-url="cloneUrl" /></template>

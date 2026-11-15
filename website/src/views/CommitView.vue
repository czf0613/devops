<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api, dateText, errorText } from '../api'
import { repositoryUrl, type Repository } from '../repositories'
import type { Backlink, Comparison } from '../collaboration'
import AppNotice from '../components/AppNotice.vue'
import DiscussionMarkdown from '../components/DiscussionMarkdown.vue'
import DiffViewer from '../components/DiffViewer.vue'
const props = defineProps<{ repository: Repository }>()
const route = useRoute(),
  error = ref('')
const commit = ref<{
  sha: string
  parents: string[]
  author: string
  date: string
  message: string
  comparison: Comparison
  backlinks: Backlink[]
}>()
onMounted(async () => {
  try {
    commit.value = await api(`/repositories/${props.repository.id}/commits/${route.params.sha}`)
  } catch (cause) {
    error.value = errorText(cause)
  }
})
</script>
<template>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice
  ><template v-if="commit"
    ><header class="commit-detail">
      <div class="heading-row">
        <h2>
          提交 <code>{{ commit.sha.slice(0, 12) }}</code>
        </h2>
        <RouterLink
          :to="{
            path: `${repositoryUrl(repository)}/issue/new`,
            query: { source: route.fullPath },
          }"
          >由此创建 Issue</RouterLink
        >
      </div>
      <p class="muted">{{ commit.author }} · {{ dateText(commit.date) }}</p>
      <DiscussionMarkdown :source="commit.message" :repository-id="repository.id" />
      <p class="muted">
        父提交
        <RouterLink
          v-for="parent in commit.parents"
          :key="parent"
          :to="`${repositoryUrl(repository)}/commit/${parent}`"
          ><code>{{ parent.slice(0, 8) }}</code> </RouterLink
        ><span v-if="!commit.parents.length">无</span>
      </p>
      <small class="commit-full-sha">{{ commit.sha }}</small>
    </header>
    <DiffViewer
      v-if="commit.comparison.files.length"
      :repository="repository"
      :comparison="commit.comparison"
      :commit="commit.sha"
    />
    <pre v-else-if="commit.comparison.patch" class="source-code">{{ commit.comparison.patch }}</pre>
    <section v-if="commit.backlinks.length" class="backlinks">
      <h3>被提及</h3>
      <p v-for="reference in commit.backlinks" :key="reference.id">
        <RouterLink :to="reference.url"
          >{{ reference.source.title
          }}<template v-if="reference.source.kind !== 'commit'">
            #{{ reference.source.number }}</template
          ></RouterLink
        >
      </p>
    </section></template
  >
  <p v-else-if="!error" class="muted">正在读取提交…</p>
</template>

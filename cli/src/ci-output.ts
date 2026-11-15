import type { CIState, CIRun, CIRunDetail, CIJob, Repository, PullCheck } from './sdk.js'

export const checkFields = ['name', 'state', 'bucket', 'description', 'link']
export function checkJSON(check: PullCheck, origin: string) {
  const buckets = {
    success: 'pass',
    failure: 'fail',
    cancelled: 'cancel',
    skipped: 'skipping',
    queued: 'pending',
    running: 'pending',
  }
  const bucket = buckets[check.status]
  if (!bucket) throw new Error(`Unknown check status: ${check.status}`)
  return {
    name: check.name,
    state: check.status === 'running' ? 'IN_PROGRESS' : check.status.toUpperCase(),
    bucket,
    description: check.details,
    link: check.external_url ? new URL(check.external_url, origin).href : '',
  }
}

export const workflowFields = ['name', 'path', 'state']
export const runFields = [
  'conclusion',
  'createdAt',
  'databaseId',
  'displayTitle',
  'event',
  'headBranch',
  'headSha',
  'name',
  'number',
  'status',
  'url',
  'workflowName',
]
export const runViewFields = [...runFields, 'jobs']
export function ciStatus(state: CIState | 'pending') {
  if (['queued', 'pending', 'assigned', 'starting'].includes(state))
    return { status: 'queued', conclusion: '' }
  if (state === 'running') return { status: 'in_progress', conclusion: '' }
  if (['success', 'failure', 'cancelled', 'timed_out', 'lost', 'skipped'].includes(state))
    return { status: 'completed', conclusion: state === 'lost' ? 'failure' : state }
  throw new Error(`Unknown CI state: ${state}`)
}
export const ciEvent = (event: string) => (event === 'manual' ? 'workflow_dispatch' : event)
export const runURL = (run: CIRun, repo: Repository, origin: string) =>
  `${origin}/repositories/${repo.full_name}/ci/${run.id}`
export function jobJSON(job: CIJob, url: string) {
  const attempt = job.attempts.at(-1)
  return {
    databaseId: job.id,
    name: job.display_name || job.name,
    ...ciStatus(job.state),
    url,
    completedAt: attempt?.finished_at ?? null,
    steps: (attempt?.steps ?? []).map((step, index) => ({
      name: step.name,
      number: index + 1,
      ...ciStatus(step.state),
    })),
  }
}
export function runJSON(run: CIRun | CIRunDetail, repo: Repository, origin: string) {
  const url = runURL(run, repo, origin)
  return {
    databaseId: run.id,
    number: run.id,
    name: run.name,
    displayTitle: run.name,
    workflowName: run.name,
    headBranch: run.ref,
    headSha: run.sha,
    event: ciEvent(run.event),
    createdAt: run.created_at,
    url,
    ...ciStatus(run.state),
    ...('jobs' in run ? { jobs: run.jobs.map((job) => jobJSON(job, url)) } : {}),
  }
}
export function runSummary(run: CIRunDetail, verbose = false, compact = false) {
  const state = ciStatus(run.state)
  const lines = [
    `${state.conclusion || state.status}\t${run.name} #${run.id}\t${run.ref}\t${run.sha}`,
    `${ciEvent(run.event)} · ${run.workflow_path}`,
  ]
  if (run.error) lines.push(run.error)
  for (const job of run.jobs) {
    const status = ciStatus(job.state),
      attempt = job.attempts.at(-1)
    lines.push(
      `  ${status.conclusion || status.status}\t${job.display_name || job.name}\t(ID ${job.id})`,
    )
    if (attempt?.error) lines.push(`    ${attempt.error}`)
    if (verbose)
      for (const step of attempt?.steps ?? []) {
        const status = ciStatus(step.state)
        if (!compact || !['success', 'skipped'].includes(step.state))
          lines.push(
            `    ${status.conclusion || status.status}\t${step.name}${step.exit_code == null ? '' : ` (exit ${step.exit_code})`}`,
          )
      }
  }
  return lines.join('\n')
}
export function runFailed(run: CIRun) {
  const value = ciStatus(run.state)
  return value.status === 'completed' && !['success', 'skipped'].includes(value.conclusion)
}

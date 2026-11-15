import { spawnSync } from 'node:child_process'
import type { Command } from 'commander'
import type { Item, Repository, Pull, Person } from './sdk.js'
export const repoFields = [
  'id',
  'name',
  'nameWithOwner',
  'description',
  'url',
  'owner',
  'isPrivate',
  'isArchived',
  'defaultBranchRef',
  'createdAt',
  'updatedAt',
  'viewerPermission',
]
export const itemFields = [
  'id',
  'number',
  'title',
  'body',
  'state',
  'url',
  'author',
  'assignees',
  'labels',
  'milestone',
  'createdAt',
  'updatedAt',
  'closedAt',
  'isLocked',
]
export const pullFields = [
  ...itemFields,
  'baseRefName',
  'headRefName',
  'baseRefOid',
  'headRefOid',
  'isDraft',
  'mergeCommit',
  'mergeStateStatus',
  'reviewRequests',
  'statusCheckRollup',
]
export function jsonOptions(command: Command, fields: string[]) {
  command
    .option('--json [fields]', 'Output JSON with the selected comma-separated fields')
    .option('-q, --jq <expression>', 'Filter JSON using the jq executable')
  command.hook('preAction', () => {
    const options = command.optsWithGlobals()
    if (options.json === true) {
      console.log('Available fields:\n' + fields.join('\n'))
      process.exit(0)
    }
    if (options.jq && !options.json) throw new Error('--jq requires --json')
    if (options.json) {
      const unknown = options.json.split(',').filter((name: string) => !fields.includes(name))
      if (unknown.length)
        throw new Error(
          `Unknown JSON field(s): ${unknown.join(', ')}. Available fields: ${fields.join(', ')}`,
        )
    }
  })
  return command
}
export const clean = (value: unknown) =>
  String(value ?? '').replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '')
const person = (value: Person) => ({
  id: value.id,
  login: value.username,
  name: value.display_name,
})
export function repositoryJSON(repo: Repository, origin: string) {
  return {
    id: repo.id,
    name: repo.name,
    nameWithOwner: repo.full_name,
    description: repo.description,
    url: origin + '/repositories/' + repo.full_name,
    owner: person(repo.owner),
    isPrivate: true,
    isArchived: repo.archived,
    defaultBranchRef: { name: repo.default_branch },
    createdAt: repo.created_at,
    updatedAt: repo.updated_at,
    viewerPermission:
      repo.permissions.is_owner || repo.permissions.is_admin
        ? 'ADMIN'
        : repo.permissions.access.toUpperCase(),
  }
}
export function itemJSON(item: Item, origin: string, pull?: Pull) {
  return {
    id: item.id,
    number: item.number,
    title: item.title,
    body: item.body ?? '',
    state: item.state.toUpperCase(),
    url: new URL(item.url, origin).href,
    author: person(item.author),
    assignees: item.assignees.map(person),
    labels: item.catalog
      .filter((c) => c.kind === 'label')
      .map((c) => ({
        id: c.id,
        name: c.name,
        description: c.description,
        color: String(c.data.color || '').replace('#', ''),
      })),
    milestone: item.catalog.find((c) => c.kind === 'milestone')
      ? { title: item.catalog.find((c) => c.kind === 'milestone')!.name }
      : null,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    closedAt: item.closed_at,
    isLocked: item.locked,
    ...(pull
      ? {
          baseRefName: pull.base_ref,
          headRefName: pull.head_ref,
          baseRefOid: pull.base_sha,
          headRefOid: pull.head_sha,
          isDraft: pull.draft,
          mergeCommit: pull.merged_sha ? { oid: pull.merged_sha } : null,
          mergeStateStatus: pull.reasons.length
            ? 'BLOCKED'
            : (pull.merge_checks.merge as any)?.status === 'clean'
              ? 'CLEAN'
              : 'UNKNOWN',
          reviewRequests: pull.reviewers.map(person),
          statusCheckRollup: pull.checks,
        }
      : {}),
  }
}
export function printJSON(value: unknown, jq?: string) {
  const data = JSON.stringify(value, null, 2) + '\n'
  if (!jq) {
    process.stdout.write(data)
    return
  }
  const result = spawnSync('jq', ['-r', jq], {
    input: data,
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    shell: false,
  })
  if (result.error) throw new Error('--jq requires jq installed on PATH')
  if (result.status) throw new Error(clean(result.stderr))
  process.stdout.write(result.stdout)
}
export function output(value: any, options: Record<string, any>, human: () => string) {
  if (options.json) {
    const fields = options.json.split(',')
    const project = (row: any) =>
      Object.fromEntries(fields.map((key: string) => [key, row[key] ?? null]))
    printJSON(Array.isArray(value) ? value.map(project) : project(value), options.jq)
  } else console.log(clean(human()))
}

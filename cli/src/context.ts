import { execFileSync, spawnSync } from 'node:child_process'
import { DevOpsClient, normalizeHost, type Repository } from './sdk.js'
import { accessToken } from './auth.js'
import { currentHost } from './storage.js'
export type Options = Record<string, any>
export function git(args: string[]): string {
  return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
}
export function parseRepo(input: string): { host?: string; owner: string; name: string } {
  let path = input,
    host: string | undefined
  if (input.includes('://')) {
    const url = new URL(input)
    host = normalizeHost(url.origin)
    if (url.username || url.password || url.search || url.hash)
      throw new Error('Repository URL must not contain credentials, query or fragment')
    path = decodeURIComponent(url.pathname).replace(/^\/(git|repositories)\//, '')
  } else {
    const parts = input.split('/')
    if (parts.length === 3) {
      host = normalizeHost(parts.shift()!)
      path = parts.join('/')
    }
  }
  const match = /^([a-z][a-z0-9_.-]{2,31})\/([a-z0-9][a-z0-9._-]{0,99})$/.exec(
    path.replace(/\.git$/, ''),
  )
  if (!match) throw new Error('Use OWNER/REPO, HOST/OWNER/REPO or a DevOps repository URL')
  return { host, owner: match[1], name: match[2] }
}
export function defaultRepo(): string {
  try {
    const saved = git(['config', '--local', '--get', 'devops.repo'])
    if (saved) return saved
  } catch {}
  let remotes: string[]
  try {
    remotes = git(['remote']).split('\n').filter(Boolean)
  } catch {
    throw new Error('Specify a repository with -R OWNER/REPO')
  }
  const candidates = new Set<string>()
  for (const remote of remotes) {
    const url = git(['remote', 'get-url', remote])
    if (/^https?:\/\/[^/]+\/git\//.test(url)) {
      const repo = parseRepo(url)
      candidates.add(`${repo.host}/git/${repo.owner}/${repo.name}.git`)
    }
  }
  if (candidates.size !== 1)
    throw new Error('Cannot determine one DevOps repository; use -R or devops repo set-default')
  return [...candidates][0]
}
export async function context(
  options: Options,
  repository?: string,
): Promise<{ client: DevOpsClient; repo: Repository }> {
  const parsed = parseRepo(repository || options.repo || process.env.DEVOPS_REPO || defaultRepo())
  const explicit = options.hostname || process.env.DEVOPS_HOST
  if (parsed.host && explicit && parsed.host !== normalizeHost(explicit))
    throw new Error('Repository host conflicts with --hostname / DEVOPS_HOST')
  const host = parsed.host || (await currentHost(options.hostname))
  const client = new DevOpsClient({ hostname: host, token: () => accessToken(host) })
  return { client, repo: await client.repository(parsed.owner, parsed.name) }
}
export async function clientFor(options: Options) {
  const host = await currentHost(options.hostname)
  return new DevOpsClient({ hostname: host, token: () => accessToken(host) })
}
export function runGit(args: string[]) {
  const result = spawnSync('git', args, { stdio: 'inherit', shell: false })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`git exited with status ${result.status}`)
}
export function positive(value: string): number {
  const number = Number(value)
  if (!Number.isSafeInteger(number) || number < 1) throw new Error('Expected a positive integer')
  return number
}

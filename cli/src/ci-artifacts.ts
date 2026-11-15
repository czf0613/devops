import { createHash } from 'node:crypto'
import { lstat, mkdir, open, realpath, unlink } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import type { CIArtifact, CIRunDetail, DevOpsClient } from './sdk.js'

function segments(value: string): string[] {
  const parts = value.split('/')
  if (
    !value ||
    /[\\:*?"<>|\x00-\x1f\x7f]/.test(value) ||
    parts.some(
      (p) =>
        !p ||
        p === '.' ||
        p === '..' ||
        p.toLowerCase() === '.git' ||
        /[. ]$/.test(p) ||
        /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p),
    )
  )
    throw new Error('Unsafe artifact path refused')
  return parts
}
export function artifactPattern(pattern: string) {
  if (/[\[\]{}()!\\]/.test(pattern))
    throw new Error('Artifact patterns support * and ? wildcards')
  return new RegExp(
    '^' +
      [...pattern]
        .map((c) => (c === '*' ? '.*' : c === '?' ? '.' : c.replace(/[.+^$|]/g, '\\$&')))
        .join('') +
      '$',
  )
}
async function exists(path: string) {
  try {
    return await lstat(path)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
    throw error
  }
}
async function safeParent(root: string, parts: string[]) {
  let parent = root
  for (const part of parts) {
    parent = join(parent, part)
    try {
      await mkdir(parent)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
    }
    const info = await lstat(parent)
    if (info.isSymbolicLink() || !info.isDirectory())
      throw new Error('Artifact directory contains a link or non-directory')
  }
}

export async function downloadArtifacts(
  client: DevOpsClient,
  repository: number,
  run: CIRunDetail,
  options: { dir: string; name: string[]; pattern: string[] },
) {
  if (options.name.length && options.pattern.length)
    throw new Error('Choose --name or --pattern')
  const patterns = options.pattern.map(artifactPattern)
  const groups = new Map<string, { artifact: CIArtifact; parts: string[] }[]>()
  for (const job of run.jobs)
    for (const artifact of job.attempts.at(-1)?.artifacts ?? []) {
      const parts = segments(artifact.name)
      const github = run.workflow_path.startsWith('.github/workflows/')
      const group = github && parts.length > 1 ? parts[0]! : artifact.name
      const inside = github && parts.length > 1 ? parts.slice(1) : [parts.at(-1)!]
      const entries = groups.get(group) ?? []
      entries.push({ artifact, parts: inside })
      groups.set(group, entries)
    }
  for (const name of options.name)
    if (!groups.has(name)) throw new Error(`Artifact not found: ${name}`)
  const selected = [...groups].filter(([name]) =>
    options.name.length
      ? options.name.includes(name)
      : !patterns.length || patterns.some((p) => p.test(name)),
  )
  if (!selected.length) throw new Error('No matching artifacts found')
  const plan = selected.flatMap(([group, entries]) =>
    entries.map((entry) => ({
      ...entry,
      parts: options.name.length === 1 ? entry.parts : [...segments(group), ...entry.parts],
    })),
  )
  const seen = new Set<string>()
  for (const entry of plan) {
    const key = entry.parts.join('/').toLowerCase()
    if (seen.has(key)) throw new Error('Artifacts contain conflicting destination paths')
    seen.add(key)
    if (
      !Number.isSafeInteger(entry.artifact.size) ||
      entry.artifact.size < 0 ||
      entry.artifact.size > 256 * 1024 * 1024 ||
      !/^[a-f0-9]{64}$/.test(entry.artifact.sha256)
    )
      throw new Error('Invalid artifact integrity metadata')
  }
  await mkdir(resolve(options.dir), { recursive: true })
  const root = await realpath(resolve(options.dir))
  for (const entry of plan) {
    await safeParent(root, entry.parts.slice(0, -1))
    if (await exists(join(root, ...entry.parts)))
      throw new Error('Artifact destination already exists; choose an empty --dir')
  }
  for (const { artifact, parts } of plan) {
    // Opening with wx never replaces existing user files, including symlinks.
    await safeParent(root, parts.slice(0, -1))
    const target = join(root, ...parts),
      file = await open(target, 'wx', 0o600)
    const controller = new AbortController()
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined,
      complete = false
    try {
      const response = await client.downloadArtifact(
        repository,
        artifact.id,
        controller.signal,
      )
      if (!response.body) throw new Error('Artifact download has no response body')
      reader = response.body.getReader()
      const hash = createHash('sha256')
      let size = 0
      while (true) {
        const chunk = await reader.read()
        if (chunk.done) break
        size += chunk.value.byteLength
        if (size > artifact.size) throw new Error('Artifact size exceeds its metadata')
        hash.update(chunk.value)
        let offset = 0
        while (offset < chunk.value.byteLength)
          offset += (await file.write(chunk.value.subarray(offset))).bytesWritten
      }
      if (size !== artifact.size || hash.digest('hex') !== artifact.sha256)
        throw new Error('Artifact size or SHA-256 verification failed')
      complete = true
    } finally {
      controller.abort()
      await reader?.cancel().catch(() => {})
      await file.close()
      if (!complete) await unlink(target)
    }
  }
  return plan.length
}

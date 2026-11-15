import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import { mkdtemp, rm, readFile, writeFile, symlink, mkdir, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { DevOpsClient } from '../dist/sdk.js'

const entry = resolve('dist/main.js')
const repo = { id: 9, full_name: 'owner/demo', default_branch: 'trunk' }
const workflow = {
  name: 'Build',
  path: '.github/workflows/build.yml',
  manual: true,
  error: '',
  inputs: {
    message: { type: 'string', required: true },
    enabled: { type: 'boolean', default: false },
    count: { type: 'number', default: 1 },
    target: { type: 'choice', options: ['one', 'two'], default: 'one' },
  },
}
const run = (id = 42, state = 'success', fields = {}) => ({
  id,
  repository_id: 9,
  name: 'Build',
  workflow_path: workflow.path,
  event: 'manual',
  ref: 'trunk',
  sha: 'a'.repeat(40),
  state,
  error: '',
  created_at: '2026-10-06T00:00:00Z',
  rerun_of: null,
  item_id: null,
  ...fields,
})
const attempt = {
  id: 77,
  number: 1,
  state: 'success',
  error: '',
  created_at: '2026-10-06T00:00:01Z',
  finished_at: '2026-10-06T00:00:02Z',
  steps: [{ name: 'Compile', state: 'success', exit_code: 0 }],
  artifacts: [],
}
const job = {
  id: 88,
  name: 'build',
  display_name: 'Build (one)',
  state: 'success',
  attempts: [attempt],
  needs: [],
  environment: {},
}
const detail = (state = 'success', jobs = [job]) => run(42, state, { jobs })
const json = (res, value, status = 200) => {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(value))
}
async function app(handler) {
  const requests = []
  const http = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const url = new URL(req.url, 'http://localhost')
    requests.push({
      method: req.method,
      path: url.pathname,
      query: url.searchParams,
      body: raw ? JSON.parse(raw) : undefined,
    })
    assert.equal(req.headers.authorization, 'Bearer synthetic')
    if (url.pathname === '/api/repositories/by-name/owner/demo') return json(res, repo)
    await handler(req, res, requests.at(-1))
  })
  await new Promise((r) => http.listen(0, '127.0.0.1', r))
  return {
    origin: `http://127.0.0.1:${http.address().port}`,
    requests,
    close: () => new Promise((r) => http.close(r)),
  }
}
async function cli(args, server, input = '') {
  const directory = await mkdtemp(join(tmpdir(), 'devops-ci-cli-'))
  try {
    return await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [entry, ...args], {
        cwd: directory,
        env: {
          ...process.env,
          DEVOPS_CONFIG_DIR: directory,
          DEVOPS_TOKEN: server ? 'synthetic' : '',
          DEVOPS_HOST: server?.origin ?? '',
          DEVOPS_REPO: 'owner/demo',
        },
        stdio: ['pipe', 'pipe', 'pipe'],
      })
      let stdout = '',
        stderr = ''
      child.stdout.on('data', (x) => (stdout += x))
      child.stderr.on('data', (x) => (stderr += x))
      child.once('error', reject)
      child.once('close', (code) => resolve({ code, stdout, stderr }))
      child.stdin.end(input)
    })
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}

test('CI JSON discovery is offline; unsupported gh options do not silently execute', async () => {
  for (const command of [
    ['run', 'list'],
    ['run', 'view'],
    ['workflow', 'list'],
    ['pr', 'checks', '1'],
  ]) {
    const result = await cli([...command, '--json'])
    assert.equal(result.code, 0, result.stderr)
    assert.match(result.stdout, /Available fields/)
  }
  const invalid = await cli(['run', 'list', '--json', 'notAField'])
  assert.equal(invalid.code, 1)
  assert.match(invalid.stderr, /Unknown JSON field/)
  const unsupported = await cli(['run', 'rerun', '42', '--failed'])
  assert.notEqual(unsupported.code, 0)
  assert.match(unsupported.stderr, /unknown option/)
  const jq = await cli(['run', 'list', '--jq', '.'])
  assert.equal(jq.code, 1)
  assert.match(jq.stderr, /requires --json/)
})

test('PR checks use gh fields, required filtering, pending/failure exits and watch', async () => {
  let checks = [
    {
      name: 'ci/build.yml/build',
      status: 'running',
      details: 'Building',
      external_url: '/repositories/owner/demo/ci/42',
    },
  ]
  let reads = 0,
    finish = false
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/items/1'))
      return json(res, { number: 1, kind: 'pull', url: '/repositories/owner/demo/pull/1' })
    assert.equal(request.path, '/api/repositories/9/pulls/1')
    if (finish && ++reads > 1) checks = checks.map((c) => ({ ...c, status: 'success' }))
    json(res, { checks, policy: { required_checks: ['ci/build.yml/build'] } })
  })
  try {
    const pending = await cli(
      ['pr', 'checks', '1', '--json', 'name,state,bucket,link,description'],
      server,
    )
    assert.equal(pending.code, 8, pending.stderr)
    assert.deepEqual(JSON.parse(pending.stdout), [
      {
        name: checks[0].name,
        state: 'IN_PROGRESS',
        bucket: 'pending',
        link: server.origin + checks[0].external_url,
        description: 'Building',
      },
    ])
    checks.push({
      name: 'optional',
      status: 'failure',
      details: 'Failed',
      external_url: '',
    })
    assert.equal((await cli(['pr', 'checks', '1'], server)).code, 1)
    assert.equal((await cli(['pr', 'checks', '1', '--required'], server)).code, 8)
    const early = await cli(['pr', 'checks', '1', '--watch', '--fail-fast'], server)
    assert.equal(early.code, 1)
    finish = true
    const watched = await cli(
      ['pr', 'checks', '1', '--required', '--watch', '-i', '1', '--json', 'bucket'],
      server,
    )
    assert.equal(watched.code, 0, watched.stderr)
    assert.deepEqual(JSON.parse(watched.stdout), [{ bucket: 'pass' }])
    assert.match(watched.stderr, /pending/)
    const invalid = await cli(['pr', 'checks', '1', '--fail-fast'], server)
    assert.equal(invalid.code, 1)
    assert.match(invalid.stderr, /requires --watch/)
  } finally {
    await server.close()
  }
})

test('workflow list honors disabled CI; workflow view prints exact YAML at selected branch', async () => {
  const source = 'name: Build\non: workflow_dispatch\n'
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/ci')) return json(res, { enabled: false })
    if (request.path.endsWith('/workflows'))
      return json(res, {
        items: [workflow],
        ref: request.query.get('ref'),
        sha: 'a'.repeat(40),
      })
    assert.equal(request.path, '/api/repositories/9/contents')
    assert.equal(request.query.get('path'), workflow.path)
    assert.equal(request.query.get('ref'), 'release')
    json(res, { type: 'blob', content: source })
  })
  try {
    assert.deepEqual(
      JSON.parse(
        (await cli(['workflow', 'list', '--json', 'name,path,state'], server)).stdout,
      ),
      [],
    )
    const all = await cli(['workflow', 'ls', '--all', '--json', 'name,path,state'], server)
    assert.equal(all.code, 0, all.stderr)
    assert.deepEqual(JSON.parse(all.stdout), [
      { name: 'Build', path: workflow.path, state: 'disabled_manually' },
    ])
    const yaml = await cli(
      ['workflow', 'view', 'build.yml', '--yaml', '--ref', 'release'],
      server,
    )
    assert.equal(yaml.code, 0, yaml.stderr)
    assert.equal(yaml.stdout, source)
  } finally {
    await server.close()
  }
})

test('workflow dispatch uses gh raw fields, @file and JSON stdin with typed definitions', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'ci-input-')),
    path = join(directory, 'input.txt')
  await writeFile(path, '引号 `x` $()\nsecond')
  const posted = []
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/workflows')) return json(res, { items: [workflow] })
    posted.push(request.body)
    json(res, detail('queued'), 201)
  })
  try {
    const raw = await cli(
      [
        'workflow',
        'run',
        'Build',
        '-f',
        'message=a=b',
        '-f',
        'enabled=true',
        '-f',
        'count=3',
        '-f',
        'target=two',
      ],
      server,
    )
    assert.equal(raw.code, 0, raw.stderr)
    assert.equal(raw.stdout.trim(), `${server.origin}/repositories/owner/demo/ci/42`)
    assert.deepEqual(posted[0], {
      ref: 'trunk',
      workflow: workflow.path,
      inputs: { message: 'a=b', enabled: true, count: 3, target: 'two' },
    })
    const file = await cli(
      ['workflow', 'run', 'build.yml', '--ref', 'release', '-F', `message=@${path}`],
      server,
    )
    assert.equal(file.code, 0, file.stderr)
    assert.equal(posted[1].inputs.message, '引号 `x` $()\nsecond')
    assert.equal(posted[1].ref, 'release')
    const stdin = await cli(
      ['workflow', 'run', workflow.path, '--json'],
      server,
      '{"message":"stdin","count":0,"enabled":false}',
    )
    assert.equal(stdin.code, 0, stdin.stderr)
    assert.deepEqual(posted[2].inputs, { message: 'stdin', count: 0, enabled: false })
    for (const args of [
      ['--json', '-f', 'message=x'],
      ['-f', 'message=x', '-f', 'message=y'],
      ['-f', 'message=x', '-f', 'enabled=bad'],
      ['-f', 'message=x', '-f', 'unknown=y'],
      [],
    ]) {
      const rejected = await cli(['workflow', 'run', 'Build', ...args], server, '{}')
      assert.equal(rejected.code, 1, JSON.stringify(args))
    }
    assert.equal(posted.length, 3)
  } finally {
    await server.close()
    await rm(directory, { recursive: true, force: true })
  }
})

test('ambiguous workflows require a path; writes are not retried on API rejection', async () => {
  let writes = 0
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/workflows'))
      return json(res, {
        items: [workflow, { ...workflow, path: '.github/workflows/other.yml' }],
      })
    writes++
    json(res, { detail: 'read only' }, 403)
  })
  try {
    const ambiguous = await cli(['workflow', 'run', 'Build', '-f', 'message=x'], server)
    assert.equal(ambiguous.code, 1)
    assert.match(ambiguous.stderr, /Ambiguous/)
    assert.equal(writes, 0)
    const refused = await cli(['workflow', 'run', workflow.path, '-f', 'message=x'], server)
    assert.equal(refused.code, 1)
    assert.match(refused.stderr, /HTTP 403/)
    assert.equal(writes, 1)
  } finally {
    await server.close()
  }
})

test('run filters apply before the limit across pages and preserve gh JSON fields and states', async () => {
  const pages = [
    [run(7, 'failure', { ref: 'other' }), run(6, 'queued')],
    [run(5, 'success'), run(4, 'success', { event: 'push' })],
    [run(3, 'success'), run(2, 'success')],
  ]
  const server = await app((_req, res, request) => {
    assert.equal(request.query.get('workflow'), workflow.path)
    const page = Number(request.query.get('page'))
    json(res, { items: pages[page - 1], page, page_size: 2, total: 6 })
  })
  try {
    const result = await cli(
      [
        'run',
        'ls',
        '-w',
        workflow.path,
        '-b',
        'trunk',
        '-c',
        'a'.repeat(40),
        '-e',
        'workflow_dispatch',
        '-s',
        'success',
        '-L',
        '2',
        '--json',
        'databaseId,status,conclusion,headSha,event,url',
      ],
      server,
    )
    assert.equal(result.code, 0, result.stderr)
    assert.equal(result.stderr, '')
    const rows = JSON.parse(result.stdout)
    assert.deepEqual(
      rows.map((r) => r.databaseId),
      [5, 3],
    )
    assert.equal(rows[0].status, 'completed')
    assert.equal(rows[0].conclusion, 'success')
    assert.equal(rows[0].event, 'workflow_dispatch')
    assert.equal(server.requests.filter((r) => r.path.endsWith('/runs')).length, 3)
  } finally {
    await server.close()
  }
})

test('run view projects job IDs and status, validates job ownership and conflicting URLs', async () => {
  const server = await app((_req, res) => json(res, detail('failure')))
  try {
    const viewed = await cli(
      ['run', 'view', '42', '--json', 'databaseId,status,conclusion,jobs', '--exit-status'],
      server,
    )
    assert.equal(viewed.code, 1, viewed.stderr)
    const data = JSON.parse(viewed.stdout)
    assert.equal(data.status, 'completed')
    assert.equal(data.conclusion, 'failure')
    assert.equal(data.jobs[0].databaseId, 88)
    assert.equal(data.jobs[0].steps[0].number, 1)
    const invalid = await cli(['run', 'view', '42', '--job', '999', '--log'], server)
    assert.equal(invalid.code, 1)
    assert.match(invalid.stderr, /does not belong/)
    const calls = server.requests.length
    const conflict = await cli(
      [
        'run',
        'view',
        `${server.origin}/repositories/owner/demo/ci/42`,
        '-R',
        'other/repository',
      ],
      server,
    )
    assert.equal(conflict.code, 1)
    assert.match(conflict.stderr, /conflicts/)
    assert.equal(server.requests.length, calls)
  } finally {
    await server.close()
  }
})

test('logs follow every cursor page for the selected job and preserve stdout without terminal escapes', async () => {
  const cursors = []
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/runs/42'))
      return json(
        res,
        detail('success', [job, { ...job, id: 89, attempts: [{ ...attempt, id: 78 }] }]),
      )
    assert.equal(request.path, '/api/repositories/9/ci/attempts/77/logs')
    const cursor = Number(request.query.get('after'))
    cursors.push(cursor)
    json(
      res,
      cursor === 0
        ? { chunks: [{ seq: 1, text: 'first\n\u001b[31m' }], next: 1, complete: false }
        : { chunks: [{ seq: 2, text: 'second\n' }], next: 2, complete: true },
    )
  })
  try {
    const result = await cli(['run', 'view', '42', '--job', '88', '--log'], server)
    assert.equal(result.code, 0, result.stderr)
    assert.deepEqual(cursors, [0, 1])
    assert.equal(result.stdout, 'first\n[31msecond\n')
    assert.match(result.stderr, /job 88/)
  } finally {
    await server.close()
  }
})

test('watch observes terminal outcome, has quiet stdout, and exit-status is opt-in', async () => {
  let calls = 0
  const server = await app((_req, res) =>
    json(res, detail(++calls === 1 ? 'running' : 'failure')),
  )
  try {
    const result = await cli(
      ['run', 'watch', '42', '--interval', '1', '--exit-status'],
      server,
    )
    assert.equal(result.code, 1)
    assert.equal(result.stdout, '')
    assert.match(result.stderr, /in_progress/)
    assert.match(result.stderr, /failure/)
    assert.equal((await cli(['run', 'watch', '42'], server)).code, 0)
  } finally {
    await server.close()
  }
})

test('cancel reports a request, while rerun returns the new immutable run URL', async () => {
  const server = await app((_req, res, request) => {
    if (request.method === 'GET') return json(res, detail())
    if (request.path.endsWith('/cancel')) return json(res, { ok: true }, 202)
    assert.equal(request.path, '/api/repositories/9/ci/runs/42/rerun')
    json(res, { ...detail('queued'), id: 43, rerun_of: 42 }, 201)
  })
  try {
    const cancelled = await cli(['run', 'cancel', '42'], server)
    assert.equal(cancelled.code, 0, cancelled.stderr)
    assert.match(cancelled.stderr, /requested/)
    const rerun = await cli(['run', 'rerun', '42'], server)
    assert.equal(rerun.code, 0, rerun.stderr)
    assert.match(rerun.stdout, /\/ci\/43\n$/)
    assert.equal(server.requests.filter((r) => r.method === 'POST').length, 2)
  } finally {
    await server.close()
  }
})

test('artifact download preserves binary bytes, grouping and no-clobber semantics', async () => {
  const bytes = Buffer.from([0, 255, 10, 200, 0])
  const artifact = {
    id: 66,
    name: 'bundle/sub/file.bin',
    size: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  }
  const directory = await mkdtemp(join(tmpdir(), 'ci-download-'))
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/runs/42'))
      return json(
        res,
        detail('success', [{ ...job, attempts: [{ ...attempt, artifacts: [artifact] }] }]),
      )
    res.writeHead(200, { 'Content-Type': 'application/octet-stream' })
    res.end(bytes)
  })
  try {
    const selected = await cli(
      ['run', 'download', '42', '-n', 'bundle', '-D', join(directory, 'one')],
      server,
    )
    assert.equal(selected.code, 0, selected.stderr)
    assert.deepEqual(await readFile(join(directory, 'one/sub/file.bin')), bytes)
    const all = await cli(
      ['run', 'download', '42', '-p', 'bund*', '-D', join(directory, 'all')],
      server,
    )
    assert.equal(all.code, 0, all.stderr)
    assert.deepEqual(await readFile(join(directory, 'all/bundle/sub/file.bin')), bytes)
    const repeat = await cli(
      ['run', 'download', '42', '-n', 'bundle', '-D', join(directory, 'one')],
      server,
    )
    assert.equal(repeat.code, 1)
    assert.match(repeat.stderr, /already exists/)
    assert.deepEqual(await readFile(join(directory, 'one/sub/file.bin')), bytes)
    const sdk = new DevOpsClient({ hostname: server.origin, token: 'synthetic' })
    assert.equal((await sdk.run(9, 42)).workflow_path, workflow.path)
  } finally {
    await server.close()
    await rm(directory, { recursive: true, force: true })
  }
})

test('unsafe artifact paths, symlinks, corrupted bytes and redirects cannot write outside output', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'ci-download-deny-'))
  let name = 'bundle/../../outside',
    hash = '0'.repeat(64),
    status = 200,
    downloads = 0
  const server = await app((_req, res, request) => {
    if (request.path.endsWith('/runs/42'))
      return json(
        res,
        detail('success', [
          {
            ...job,
            attempts: [
              { ...attempt, artifacts: [{ id: 66, name, size: 3, sha256: hash }] },
            ],
          },
        ]),
      )
    downloads++
    res.writeHead(status, { Location: 'http://127.0.0.1:1/api/steal' })
    res.end('bad')
  })
  try {
    const command = () =>
      cli(['run', 'download', '42', '-n', 'bundle', '-D', join(directory, 'out')], server)
    assert.equal((await command()).code, 1)
    assert.equal(downloads, 0)
    name = 'bundle/sub/file'
    await mkdir(join(directory, 'out'), { recursive: true })
    await mkdir(join(directory, 'outside'))
    await symlink(join(directory, 'outside'), join(directory, 'out/sub'), 'dir')
    assert.equal((await command()).code, 1)
    assert.equal(downloads, 0)
    await rm(join(directory, 'out/sub'))
    const corrupt = await command()
    assert.equal(corrupt.code, 1)
    assert.match(corrupt.stderr, /SHA-256/)
    await assert.rejects(access(join(directory, 'out/sub/file')))
    hash = createHash('sha256').update('bad').digest('hex')
    status = 302
    const redirect = await command()
    assert.equal(redirect.code, 1)
    assert.match(redirect.stderr, /HTTP 302/)
    await assert.rejects(access(join(directory, 'out/sub/file')))
    assert.equal(downloads, 2)
  } finally {
    await server.close()
    await rm(directory, { recursive: true, force: true })
  }
})

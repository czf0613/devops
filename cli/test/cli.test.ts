import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, rm, readFile, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { DevOpsClient, APIError, normalizeHost } from '../dist/sdk.js'
import { parseRepo } from '../dist/context.js'
import { saveLogin, readConfig, withCredential } from '../dist/storage.js'
import { accessToken } from '../dist/auth.js'

const main = resolve('dist/main.js')
async function server(handler) {
  const server = createServer(handler)
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}
const json = (res, data, status = 200) => {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(data))
}
function cli(args, env = {}, input = '') {
  return new Promise((resolve, reject) => {
    const process = spawn(globalThis.process.execPath, [main, ...args], {
      env: { ...globalThis.process.env, DEVOPS_TOKEN: '', DEVOPS_HOST: '', ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = '',
      stderr = ''
    process.stdout.on('data', (value) => {
      stdout += value
    })
    process.stderr.on('data', (value) => {
      stderr += value
    })
    process.once('error', reject)
    process.once('close', (code) => resolve({ code, stdout, stderr }))
    process.stdin.end(input)
  })
}
const owner = { id: 1, username: 'owner', display_name: 'Owner' }
const repository = {
  id: 9,
  name: 'demo',
  full_name: 'owner/demo',
  description: 'Demo',
  clone_path: '/git/owner/demo.git',
  owner,
  permissions: { access: 'write' },
  archived: false,
  default_branch: 'main',
}
const item = (number) => ({
  id: number,
  number,
  title: 'Item ' + number,
  kind: 'issue',
  state: 'open',
  version: 7,
  author: owner,
  assignees: [],
  catalog: [],
  url: '/repositories/owner/demo/issue/' + number,
  body: 'before',
})

test('origin and repository parsing refuse credentials, insecure remote hosts and invalid paths', () => {
  assert.equal(normalizeHost('example.com:8443'), 'https://example.com:8443')
  assert.equal(normalizeHost('https://EXAMPLE.com:443/'), 'https://example.com')
  for (const value of [
    'http://example.com',
    'https://u:p@example.com',
    'https://example.com/a',
    'https://example.com/?x=y',
  ])
    assert.throws(() => normalizeHost(value))
  assert.deepEqual(parseRepo('https://example.com:8443/git/owner/demo.git'), {
    host: 'https://example.com:8443',
    owner: 'owner',
    name: 'demo',
  })
  assert.throws(() => parseRepo('https://user:secret@example.com/git/owner/demo.git'))
})

test('SDK reports API errors with request IDs, rejects redirects and never retries writes', async () => {
  let calls = 0
  const app = await server((req, res) => {
    calls++
    res.setHeader('X-Request-ID', 'fixture')
    json(res, { detail: 'conflict' }, req.url.includes('redirect') ? 302 : 409)
  })
  try {
    const sdk = new DevOpsClient({ hostname: app.origin, token: 'synthetic' })
    await assert.rejects(
      sdk.request('/api/test', { method: 'POST', body: {} }),
      (e) => e instanceof APIError && e.status === 409 && e.requestId === 'fixture',
    )
    assert.equal(calls, 1)
    await assert.rejects(sdk.request('/api/redirect'), (e) => e.status === 302)
    await assert.rejects(sdk.request('https://elsewhere.example/api/test'))
    await assert.rejects(sdk.request('/api/../../oauth/token'))
    assert.equal(calls, 2)
  } finally {
    await app.close()
  }
})

test('list fetches beyond one server page, preserves gh field casing and has clean stdout', async () => {
  let count = 0
  const app = await server((req, res) => {
    assert.equal(req.headers.authorization, 'Bearer synthetic')
    if (req.url.startsWith('/api/repositories/by-name')) return json(res, repository)
    const page = Number(new URL(req.url, 'http://localhost').searchParams.get('page'))
    count++
    json(res, {
      items: Array.from({ length: page === 1 ? 25 : 10 }, (_, i) => item((page - 1) * 25 + i + 1)),
      total: 35,
      page,
      page_size: 25,
    })
  })
  try {
    const result = await cli(
      ['issue', 'list', '-R', 'owner/demo', '--limit', '30', '--json', 'number,title,state,author'],
      { DEVOPS_HOST: app.origin, DEVOPS_TOKEN: 'synthetic' },
    )
    assert.equal(result.code, 0, result.stderr)
    const items = JSON.parse(result.stdout)
    assert.equal(items.length, 30)
    assert.equal(items[29].state, 'OPEN')
    assert.equal(items[0].author.login, 'owner')
    assert.equal(count, 2)
    assert.equal(result.stderr, '')
  } finally {
    await app.close()
  }
})

test('JSON field discovery is offline and unsupported fields fail before authentication', async () => {
  const result = await cli(['issue', 'list', '--json'])
  assert.equal(result.code, 0)
  assert.match(result.stdout, /number/)
  const invalid = await cli(['issue', 'list', '--json', 'unknown'])
  assert.equal(invalid.code, 1)
  assert.match(invalid.stderr, /Unknown JSON field/)
})

test('body-file supports special characters and edits carry optimistic version without retries', async () => {
  let writes = 0,
    posted
  const app = await server(async (req, res) => {
    if (req.url.includes('by-name')) return json(res, repository)
    if (req.method === 'GET') return json(res, item(1))
    let raw = ''
    for await (const chunk of req) raw += chunk
    posted = JSON.parse(raw)
    writes++
    json(res, { detail: 'concurrent edit' }, 409)
  })
  try {
    const result = await cli(
      ['issue', 'edit', '1', '-R', 'owner/demo', '--body-file', '-'],
      { DEVOPS_HOST: app.origin, DEVOPS_TOKEN: 'synthetic' },
      'hello\n`literal` $() comma, end',
    )
    assert.equal(result.code, 1)
    assert.equal(posted.version, 7)
    assert.equal(posted.body, 'hello\n`literal` $() comma, end')
    assert.equal(writes, 1)
  } finally {
    await app.close()
  }
})

test('raw API preserves commas and stdin JSON; paginate slurp returns all pages', async () => {
  const app = await server(async (req, res) => {
    if (req.method === 'POST') {
      let raw = ''
      for await (const chunk of req) raw += chunk
      return json(res, JSON.parse(raw))
    }
    const page = Number(new URL(req.url, 'http://localhost').searchParams.get('page'))
    json(res, { items: [page], total: 2, page, page_size: 1 })
  })
  const env = { DEVOPS_HOST: app.origin, DEVOPS_TOKEN: 'synthetic' }
  try {
    const fields = await cli(['api', '/api/test', '-f', 'title=a,b', '-F', 'active=true'], env)
    assert.deepEqual(JSON.parse(fields.stdout), { title: 'a,b', active: true })
    const body = await cli(['api', '/api/test', '--input', '-'], env, '{"x":[1,2]}')
    assert.deepEqual(JSON.parse(body.stdout), { x: [1, 2] })
    const pages = await cli(['api', '/api/test', '--paginate', '--slurp'], env)
    assert.equal(JSON.parse(pages.stdout).length, 2)
  } finally {
    await app.close()
  }
})

test('environment credentials cannot cross a selected repository host', async () => {
  const result = await cli(['repo', 'view', 'https://another.example/git/owner/demo.git'], {
    DEVOPS_HOST: 'https://one.example',
    DEVOPS_TOKEN: 'synthetic',
  })
  assert.equal(result.code, 1)
  assert.match(result.stderr, /conflicts/)
})

test('refresh lock spans concurrent clients and persists unknown-outcome marker', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'devops-cli-'))
  const previous = process.env.DEVOPS_CONFIG_DIR
  process.env.DEVOPS_CONFIG_DIR = dir
  let calls = 0,
    fail = false
  const app = await server(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    calls++
    await new Promise((resolve) => setTimeout(resolve, 60))
    if (fail) {
      res.destroy()
      return
    }
    assert.equal(new URLSearchParams(raw).get('refresh_token'), 'old-refresh')
    json(res, {
      access_token: 'fresh',
      refresh_token: 'new-refresh',
      expires_in: 3600,
      scope: 'api:read',
    })
  })
  try {
    await saveLogin(
      app.origin,
      'owner',
      { access_token: 'old', refresh_token: 'old-refresh', expires_at: 0, scope: 'api:read' },
      true,
    )
    const result = await Promise.all([
      accessToken(app.origin),
      accessToken(app.origin),
      accessToken(app.origin),
    ])
    assert.deepEqual(result, ['fresh', 'fresh', 'fresh'])
    assert.equal(calls, 1)
    const config = await readConfig()
    assert.equal(config.hosts[app.origin].accounts.owner.storage, 'file')
    assert.equal((await stat(join(dir, 'config.json'))).mode & 0o777, 0o600)
    assert.ok(!(await readFile(join(dir, 'config.json'), 'utf8')).includes('new-refresh'))
    fail = true
    await assert.rejects(accessToken(app.origin, true))
    await assert.rejects(accessToken(app.origin), /unknown outcome/)
    assert.equal(calls, 2)
    await withCredential(app.origin, async (value) => assert.equal(value.refresh_pending, true))
  } finally {
    if (previous === undefined) delete process.env.DEVOPS_CONFIG_DIR
    else process.env.DEVOPS_CONFIG_DIR = previous
    await app.close()
    await rm(dir, { recursive: true, force: true })
  }
})

test('merge waits for complete and reports pending with exit 8', async () => {
  let polls = 0,
    finish = true
  const app = await server(async (req, res) => {
    if (req.url.includes('by-name')) return json(res, repository)
    if (req.url.includes('/items/')) return json(res, { ...item(1), kind: 'pull' })
    if (req.url.endsWith('/merge')) return json(res, { id: 3, state: 'pending' }, 202)
    if (req.url.includes('/merges/')) {
      polls++
      return json(res, { id: 3, state: finish ? 'complete' : 'pending', result_sha: 'merged-sha' })
    }
    return json(res, { base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40) })
  })
  try {
    const env = { DEVOPS_HOST: app.origin, DEVOPS_TOKEN: 'synthetic' }
    const complete = await cli(['pr', 'merge', '1', '-R', 'owner/demo'], env)
    assert.equal(complete.code, 0, complete.stderr)
    assert.match(complete.stdout, /merged-sha/)
    assert.equal(polls, 1)
    finish = false
    const pending = await cli(['pr', 'merge', '1', '-R', 'owner/demo', '--timeout', '1'], env)
    assert.equal(pending.code, 8)
    assert.equal(pending.stdout, '')
    assert.match(pending.stderr, /still pending/)
  } finally {
    await app.close()
  }
})
test(
  'web login validates state and PKCE and stores only after token exchange',
  { timeout: 15000 },
  async () => {
    const { createHash } = await import('node:crypto')
    const dir = await mkdtemp(join(tmpdir(), 'devops-cli-web-'))
    let authorization, assertionError
    const app = await server(async (req, res) => {
      if (req.url === '/api/cli/me') return json(res, { login: 'owner', scopes: ['api:read'] })
      let body = ''
      for await (const chunk of req) body += chunk
      try {
        const fields = new URLSearchParams(body)
        assert.equal(fields.get('client_id'), 'devops-cli')
        assert.equal(fields.get('code'), 'fixture-code')
        assert.equal(
          createHash('sha256').update(fields.get('code_verifier')).digest('base64url'),
          authorization.searchParams.get('code_challenge'),
        )
        json(res, {
          access_token: 'a'.repeat(64),
          refresh_token: 'r'.repeat(64),
          expires_in: 3600,
          scope: 'api:read',
        })
      } catch (error) {
        assertionError = error
        json(res, { error: 'invalid_grant' }, 400)
      }
    })
    const child = spawn(
      process.execPath,
      [
        main,
        'auth',
        'login',
        '--web',
        '--no-browser',
        '--insecure-storage',
        '--scopes',
        'api:read',
      ],
      {
        env: { ...process.env, DEVOPS_HOST: app.origin, DEVOPS_TOKEN: '', DEVOPS_CONFIG_DIR: dir },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
    let stderr = '',
      stdout = '',
      started = false
    child.stdout.on('data', (value) => {
      stdout += value
    })
    child.stderr.on('data', (value) => {
      stderr += value
      if (started) return
      const line = stderr
        .split('\n')
        .find((value) => value.startsWith(app.origin + '/oauth/authorize?'))
      if (!line) return
      started = true
      authorization = new URL(line)
      void (async () => {
        const callback = new URL(authorization.searchParams.get('redirect_uri'))
        const state = authorization.searchParams.get('state')
        callback.search = new URLSearchParams({
          state: '错'.repeat(state.length),
          code: 'fixture-code',
        })
        assert.equal((await fetch(callback)).status, 400)
        callback.search = new URLSearchParams({ state, code: 'fixture-code' })
        assert.equal((await fetch(callback)).status, 200)
      })().catch((error) => {
        assertionError = error
        child.kill()
      })
    })
    try {
      const code = await new Promise((resolve, reject) => {
        child.once('error', reject)
        child.once('close', resolve)
      })
      if (assertionError) throw assertionError
      assert.equal(code, 0, stderr)
      assert.equal(stdout, '')
      assert.ok(!stderr.includes('a'.repeat(64)))
      const config = JSON.parse(await readFile(join(dir, 'config.json'), 'utf8'))
      assert.equal(config.hosts[app.origin].active, 'owner')
    } finally {
      child.kill()
      await app.close()
      await rm(dir, { recursive: true, force: true })
    }
  },
)

test('repository lifecycle requires an email code, supports explicit delivery, and never prints the code', async () => {
  const writes = []
  const app = await server(async (req, res) => {
    if (req.url.startsWith('/api/repositories/by-name')) return json(res, repository)
    let body = ''
    for await (const chunk of req) body += chunk
    writes.push({ path: req.url, method: req.method, body: body ? JSON.parse(body) : null })
    json(res, { message: 'OK' })
  })
  const env = { DEVOPS_HOST: app.origin, DEVOPS_TOKEN: 'synthetic' }
  try {
    const missing = await cli(['repo', 'archive', 'owner/demo', '--yes'], env)
    assert.equal(missing.code, 1)
    assert.match(missing.stderr, /--code-stdin/)
    assert.equal(writes.length, 0)
    const sent = await cli(['repo', 'archive', 'owner/demo', '--request-code'], env)
    assert.equal(sent.code, 0, sent.stderr)
    assert.equal(writes[0].path, '/api/repositories/9/operation-code')
    assert.equal(writes.length, 1)
    const invalid = await cli(['repo', 'archive', 'owner/demo', '--yes', '--code-stdin'], env, 'bad')
    assert.equal(invalid.code, 1)
    assert.equal(writes.length, 1)
    const done = await cli(['repo', 'delete', 'owner/demo', '--yes', '--code-stdin'], env, '123456\n')
    assert.equal(done.code, 0, done.stderr)
    assert.deepEqual(writes[1], { path: '/api/repositories/9', method: 'DELETE', body: { confirm_name: 'owner/demo', code: '123456' } })
    assert.equal((done.stdout + done.stderr).includes('123456'), false)
  } finally { await app.close() }
})

import { createServer } from 'node:http'
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto'
import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'
import { DevOpsClient, DEFAULT_HOST, normalizeHost } from './sdk.js'
import { saveLogin, withCredential, type Credential } from './storage.js'

const CLIENT = 'devops-cli'
type Tokens = { access_token: string; refresh_token: string; expires_in: number; scope: string }
export class OAuthError extends Error {
  constructor(public code: string) {
    super(`OAuth: ${code}`)
  }
}
export async function oauth(
  host: string,
  endpoint: string,
  values: Record<string, string>,
): Promise<any> {
  const response = await fetch(host + endpoint, {
    method: 'POST',
    redirect: 'manual',
    signal: AbortSignal.timeout(30000),
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
    body: new URLSearchParams({ client_id: CLIENT, ...values }),
  })
  const result = (await response.json().catch(() => ({}))) as any
  if (!response.ok) throw new OAuthError(result.error || `HTTP ${response.status}`)
  return result
}
const credential = (tokens: Tokens): Credential => ({
  access_token: tokens.access_token,
  refresh_token: tokens.refresh_token,
  scope: tokens.scope,
  expires_at: Date.now() + tokens.expires_in * 1000,
})
export async function accessToken(host: string, force = false): Promise<string> {
  if (process.env.DEVOPS_TOKEN) {
    if (host !== normalizeHost(process.env.DEVOPS_HOST || DEFAULT_HOST))
      throw new Error('DEVOPS_TOKEN is bound to DEVOPS_HOST; refusing to send it to another host')
    if (force) throw new Error('Environment token cannot be refreshed')
    return process.env.DEVOPS_TOKEN
  }
  return withCredential(host, async (value, save) => {
    if (value.refresh_pending)
      throw new Error(
        'A previous refresh has an unknown outcome; run devops auth login again (the refresh token will not be replayed)',
      )
    if (!force && value.expires_at > Date.now() + 60000) return value.access_token
    if (!value.refresh_token) throw new Error('Token expired; run devops auth login')
    // Persist intent before the rotating request. A crash or lost response must
    // never cause another process to replay the old refresh token.
    await save({ ...value, refresh_pending: true })
    const tokens = await oauth(host, '/oauth/token', {
      grant_type: 'refresh_token',
      refresh_token: value.refresh_token,
    })
    const fresh = credential(tokens)
    await save(fresh)
    return fresh.access_token
  })
}
export async function openBrowser(url: string) {
  const parsed = new URL(url)
  if (!['http:', 'https:'].includes(parsed.protocol))
    throw new Error('Only HTTP(S) browser URLs are supported')
  const [exe, args] =
    process.platform === 'darwin'
      ? ['open', [url]]
      : process.platform === 'win32'
        ? ['rundll32.exe', ['url.dll,FileProtocolHandler', url]]
        : ['xdg-open', [url]]
  await new Promise<void>((resolve, reject) => {
    const child = spawn(exe, args, { stdio: 'ignore', shell: false })
    child.once('error', reject)
    child.once('exit', (code) =>
      code === 0 ? resolve() : reject(new Error('Could not open a browser')),
    )
  })
}
async function webLogin(host: string, scopes: string, browser = true): Promise<Tokens> {
  const verifier = randomBytes(48).toString('base64url'),
    state = randomBytes(32).toString('base64url')
  let resolveCode!: (code: string) => void, rejectCode!: (error: Error) => void
  const outcome = new Promise<string>((resolve, reject) => {
    resolveCode = resolve
    rejectCode = reject
  })
  // A user may take time to open the page; attach a handler before awaiting open.
  void outcome.catch(() => {})
  let redirect = '',
    settled = false
  const server = createServer((request, response) => {
    const url = new URL(request.url || '/', redirect)
    const receivedState = url.searchParams.get('state') || ''
    const receivedBytes = Buffer.from(receivedState),
      expectedBytes = Buffer.from(state)
    if (
      request.method !== 'GET' ||
      url.pathname !== '/' ||
      request.headers.host !== new URL(redirect).host ||
      receivedBytes.length !== expectedBytes.length ||
      !timingSafeEqual(receivedBytes, expectedBytes)
    ) {
      response.writeHead(400).end('Invalid authorization callback')
      return
    }
    if (settled) {
      response.writeHead(409).end('Callback already received')
      return
    }
    settled = true
    response.writeHead(200, {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
    })
    if (url.searchParams.has('error')) {
      response.end('Authorization cancelled. Return to the terminal.')
      rejectCode(new OAuthError(url.searchParams.get('error')!))
      return
    }
    const code = url.searchParams.get('code')
    if (!code) {
      response.end('Missing authorization code')
      rejectCode(new Error('Missing code'))
      return
    }
    response.end(
      'Authorization received. Return to the terminal to finish. You can close this page.',
    )
    resolveCode(code)
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Loopback listener unavailable')
  redirect = `http://127.0.0.1:${address.port}/`
  const timeout = setTimeout(
    () => rejectCode(new Error('Login timed out; run auth login again')),
    10 * 60 * 1000,
  )
  try {
    const url = new URL('/oauth/authorize', host)
    url.search = new URLSearchParams({
      client_id: CLIENT,
      response_type: 'code',
      redirect_uri: redirect,
      scope: scopes,
      state,
      code_challenge: createHash('sha256').update(verifier).digest('base64url'),
      code_challenge_method: 'S256',
    }).toString()
    console.error('Open this URL to authorize DevOps CLI:\n' + url.href)
    if (browser)
      await openBrowser(url.href).catch(() => console.error('Open the URL above manually.'))
    const code = await outcome
    return await oauth(host, '/oauth/token', {
      grant_type: 'authorization_code',
      code,
      code_verifier: verifier,
      redirect_uri: redirect,
    })
  } finally {
    clearTimeout(timeout)
    server.close()
    server.closeAllConnections()
  }
}
async function deviceLogin(host: string, scopes: string): Promise<Tokens> {
  const device = await oauth(host, '/oauth/device/code', { scope: scopes })
  const verification = new URL(device.verification_uri)
  if (verification.origin !== host)
    throw new Error('Server returned a different authorization host')
  console.error(
    `Open ${verification.href}\nEnter code: ${device.user_code}\nWaiting for authorization…`,
  )
  let interval = Number(device.interval) || 5
  const deadline = Date.now() + Number(device.expires_in) * 1000
  while (Date.now() < deadline) {
    await sleep(interval * 1000)
    try {
      return await oauth(host, '/oauth/token', {
        grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
        device_code: device.device_code,
      })
    } catch (error) {
      if (error instanceof OAuthError && error.code === 'authorization_pending') continue
      if (error instanceof OAuthError && error.code === 'slow_down') {
        interval += 5
        continue
      }
      throw error
    }
  }
  throw new Error('Device code expired; start login again')
}
export async function login(
  host: string,
  options: {
    device?: boolean
    web?: boolean
    insecureStorage?: boolean
    scopes?: string
    withToken?: boolean
    browser?: boolean
  },
) {
  if (options.device && options.web) throw new Error('Choose --web or --device')
  const scopes = options.scopes?.replaceAll(',', ' ') || 'api:read api:write'
  if (
    !scopes.split(/\s+/).every((v) => ['api:read', 'api:write'].includes(v)) ||
    !scopes.includes('api:read')
  )
    throw new Error('Supported scopes: api:read and api:write (api:read required)')
  let value: Credential
  if (options.withToken) {
    if (process.stdin.isTTY) throw new Error('--with-token reads a token from standard input')
    let raw = ''
    for await (const chunk of process.stdin) raw += chunk
    const token = raw.trim()
    if (!/^[A-Za-z0-9_-]{64}$/.test(token)) throw new Error('Invalid access token')
    value = { access_token: token, expires_at: Number.MAX_SAFE_INTEGER, scope: scopes }
  } else
    value = credential(
      await (options.device ? deviceLogin(host, scopes) : webLogin(host, scopes, options.browser)),
    )
  const user = await new DevOpsClient({ hostname: host, token: value.access_token }).me()
  try {
    await saveLogin(host, user.login, value, !!options.insecureStorage)
  } catch (error) {
    // Avoid leaving an unrecorded grant after a failed local save.
    await oauth(host, '/oauth/revoke', { token: value.access_token }).catch(() => {})
    throw error
  }
  console.error(
    `Logged in to ${host} as ${user.login}${options.insecureStorage ? ' (plaintext credential file, mode 0600)' : ' (system credential store)'}`,
  )
}
export async function revoke(host: string) {
  if (process.env.DEVOPS_TOKEN) throw new Error('Unset DEVOPS_TOKEN to revoke the saved account')
  await withCredential(host, (value) =>
    oauth(host, '/oauth/revoke', { token: value.refresh_token || value.access_token }),
  )
}

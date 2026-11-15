import { mkdir, readFile, writeFile, rename, chmod, lstat, unlink } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import lockfile from 'proper-lockfile'
import { DEFAULT_HOST, normalizeHost } from './sdk.js'

export const configDir = () =>
  process.env.DEVOPS_CONFIG_DIR ||
  join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), 'devops')
export interface Credential {
  access_token: string
  refresh_token?: string
  expires_at: number
  scope: string
  refresh_pending?: boolean
}
interface Host {
  active: string
  accounts: Record<string, { storage: 'keyring' | 'file' }>
}
interface Config {
  hostname?: string
  hosts: Record<string, Host>
}
const filename = () => join(configDir(), 'config.json')
async function ensure() {
  await mkdir(configDir(), { recursive: true, mode: 0o700 })
  const stat = await lstat(configDir())
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error('Config directory must be a real directory')
}
export async function readConfig(): Promise<Config> {
  try {
    return JSON.parse(await readFile(filename(), 'utf8'))
  } catch (error: any) {
    if (error.code === 'ENOENT') return { hosts: {} }
    throw error
  }
}
async function atomic(path: string, data: unknown) {
  const temp = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600, flag: 'wx' })
    await rename(temp, path)
    await chmod(path, 0o600)
  } finally {
    await unlink(temp).catch(() => {})
  }
}
export async function locked<T>(fn: () => Promise<T>): Promise<T> {
  await ensure()
  const release = await lockfile.lock(configDir(), {
    stale: 120000,
    update: 10000,
    retries: { retries: 40, minTimeout: 250, maxTimeout: 1000 },
  })
  try {
    return await fn()
  } finally {
    await release()
  }
}
export async function currentHost(explicit?: string) {
  return normalizeHost(
    explicit || process.env.DEVOPS_HOST || (await readConfig()).hostname || DEFAULT_HOST,
  )
}
const credentialFile = (host: string, login: string) =>
  join(
    configDir(),
    `credential-${createHash('sha256')
      .update(host + '\n' + login)
      .digest('hex')}.json`,
  )
async function entry(host: string, login: string) {
  try {
    const { AsyncEntry } = await import('@napi-rs/keyring')
    return new AsyncEntry('devops-cli:' + host, login, { linux: { store: 'secret-service' } })
  } catch {
    throw new Error(
      'System credential store unavailable. For an unattended machine explicitly use auth login --insecure-storage, or DEVOPS_TOKEN with DEVOPS_HOST.',
    )
  }
}
async function readCredential(host: string, login: string, storage: string): Promise<Credential> {
  const raw =
    storage === 'file'
      ? await readFile(credentialFile(host, login), 'utf8')
      : await (await entry(host, login)).getPassword()
  if (!raw) throw new Error('No stored credential; run devops auth login')
  return JSON.parse(raw)
}
async function writeCredential(
  host: string,
  login: string,
  storage: string,
  credential: Credential,
) {
  if (storage === 'file') await atomic(credentialFile(host, login), credential)
  else await (await entry(host, login)).setPassword(JSON.stringify(credential))
}
export async function saveLogin(
  host: string,
  login: string,
  credential: Credential,
  insecure: boolean,
) {
  await locked(async () => {
    const config = await readConfig()
    const storage = insecure ? 'file' : 'keyring'
    await writeCredential(host, login, storage, credential)
    const previous = config.hosts[host]?.accounts[login]?.storage
    if (previous && previous !== storage) {
      if (previous === 'file') await unlink(credentialFile(host, login))
      else await (await entry(host, login)).deletePassword()
    }
    const saved = config.hosts[host] ?? { active: login, accounts: {} }
    saved.accounts[login] = { storage }
    saved.active = login
    config.hosts[host] = saved
    config.hostname = host
    await atomic(filename(), config)
  })
}
export async function withCredential<T>(
  host: string,
  fn: (
    credential: Credential,
    save: (value: Credential) => Promise<void>,
    login: string,
  ) => Promise<T>,
): Promise<T> {
  return locked(async () => {
    const config = await readConfig(),
      hostConfig = config.hosts[host]
    if (!hostConfig?.active) throw new Error('Authentication required. Run devops auth login')
    const login = hostConfig.active,
      account = hostConfig.accounts[login]
    return fn(
      await readCredential(host, login, account.storage),
      (value) => writeCredential(host, login, account.storage, value),
      login,
    )
  })
}
export async function switchAccount(host: string, login: string) {
  await locked(async () => {
    const config = await readConfig()
    if (!config.hosts[host]?.accounts[login])
      throw new Error('Account is not logged in on this host')
    config.hosts[host].active = login
    await atomic(filename(), config)
  })
}
export async function logout(host: string) {
  await locked(async () => {
    const config = await readConfig(),
      saved = config.hosts[host]
    if (!saved) throw new Error('No saved account for this host')
    const login = saved.active
    if (saved.accounts[login].storage === 'file') await unlink(credentialFile(host, login))
    else await (await entry(host, login)).deletePassword()
    delete saved.accounts[login]
    saved.active = Object.keys(saved.accounts)[0]
    if (!saved.active) delete config.hosts[host]
    await atomic(filename(), config)
  })
}

#!/usr/bin/env node
import { Command, Option } from 'commander'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createInterface } from 'node:readline/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import {
  APIError,
  DevOpsClient,
  type Catalog,
  type Item,
  type Pull,
  type Repository,
} from './sdk.js'
import { accessToken, login, openBrowser, revoke, OAuthError } from './auth.js'
import { currentHost, logout, switchAccount } from './storage.js'
import { registerCI, pollInterval } from './ci.js'
import { checkFields, checkJSON } from './ci-output.js'
import { clientFor, context, git, parseRepo, positive, runGit, type Options } from './context.js'
import {
  clean,
  itemFields,
  itemJSON,
  jsonOptions,
  output,
  printJSON,
  pullFields,
  repoFields,
  repositoryJSON,
} from './output.js'

const program = new Command()
  .name('devops')
  .description('DevOps repository, issue, pull request and CI CLI')
  .version(createRequire(import.meta.url)('../package.json').version)
  .option('--hostname <origin>', 'DevOps origin including optional port (or DEVOPS_HOST)')
  .option('-R, --repo <repository>', 'OWNER/REPO, HOST/OWNER/REPO or repository URL')
program.showHelpAfterError()
const opts = (cmd: Command) => cmd.optsWithGlobals()
const act = (command: Command, fn: (...args: any[]) => void | Promise<void>) => command.action(fn)
const collect = (value: string, previous: string[]) => [
  ...previous,
  ...value.split(',').filter(Boolean),
]
const append = (value: string, previous: string[]) => [...previous, value]
async function inputBody(options: Options, required = false) {
  if (options.body !== undefined && options.bodyFile !== undefined)
    throw new Error('Choose --body or --body-file')
  if (options.bodyFile !== undefined) {
    if (options.bodyFile !== '-') return readFile(options.bodyFile, 'utf8')
    let text = ''
    for await (const chunk of process.stdin) text += chunk
    return text
  }
  if (required && options.body === undefined)
    throw new Error('Provide --body or --body-file (use - for stdin)')
  return options.body
}
const bodyOptions = (c: Command) =>
  c
    .option('-b, --body <text>', 'Body text')
    .option('-F, --body-file <file>', 'Read body from file (- for stdin)')
function metadataOptions(c: Command) {
  return c
    .option('-a, --assignee <login>', 'Assign users (repeat or comma-separate)', collect, [])
    .option('-l, --label <name>', 'Apply labels (repeat or comma-separate)', collect, [])
    .option('-m, --milestone <name>', 'Milestone name')
}
async function metadata(client: DevOpsClient, repo: Repository, options: Options) {
  const result: { assignee_ids?: number[]; catalog_ids?: number[] } = {}
  if (options.assignee?.length) {
    const members = await client.request<any[]>(`/api/repositories/${repo.id}/members`)
    const me = options.assignee.includes('@me') ? await client.me() : undefined
    result.assignee_ids = options.assignee.map((login: string) => {
      const member = members.find((m) => m.username === (login === '@me' ? me?.login : login))
      if (!member) throw new Error(`Repository member not found: ${login}`)
      return member.user_id
    })
  }
  if (options.label?.length || options.milestone) {
    const catalog = await client.request<Catalog[]>(`/api/repositories/${repo.id}/catalog`)
    result.catalog_ids = [
      ...(options.label || []).map((name: string) => ['label', name]),
      ...(options.milestone ? [['milestone', options.milestone]] : []),
    ].map(([kind, name]) => {
      const entry = catalog.find((c) => c.kind === kind && c.name === name)
      if (!entry) throw new Error(`${kind} not found: ${name}`)
      return entry.id
    })
  }
  return result
}
function selection(value: string, options: Options) {
  if (/^https?:\/\//.test(value)) {
    const url = new URL(value),
      match = /^\/repositories\/([^/]+)\/([^/]+)\/(?:issue|pr)\/(\d+)$/.exec(url.pathname)
    if (!match || url.username || url.password) throw new Error('Invalid issue / PR URL')
    const fromURL = url.origin + '/repositories/' + match[1] + '/' + match[2]
    if (options.repo) {
      const explicit = parseRepo(options.repo),
        actual = parseRepo(fromURL)
      if (
        explicit.owner !== actual.owner ||
        explicit.name !== actual.name ||
        (explicit.host && explicit.host !== actual.host)
      )
        throw new Error('URL conflicts with --repo')
    }
    options.repo = fromURL
    return positive(match[3])
  }
  return positive(value.replace(/^#/, ''))
}
async function itemContext(kind: string, value: string, options: Options) {
  const number = selection(value, options),
    ctx = await context(options),
    item = await ctx.client.item(ctx.repo.id, number)
  if (item.kind !== kind) throw new Error(`#${number} is not a ${kind}`)
  return { ...ctx, item, number }
}

const auth = program.command('auth').description('Authenticate with a browser or device code')
act(
  auth
    .command('login')
    .description('Authorize a public CLI client')
    .option('-w, --web', 'Use local browser authorization (default)')
    .option('--no-browser', 'Print the authorization URL without opening a browser')
    .option('--device', 'Use a code on another device')
    .option('--insecure-storage', 'Explicitly store plaintext credentials in a mode-0600 file')
    .option('-s, --scopes <scopes>', 'api:read,api:write')
    .option('--with-token', 'Read an existing CLI access token from stdin'),
  async (_o, cmd) => login(await currentHost(opts(cmd).hostname), opts(cmd)),
)
act(
  jsonOptions(auth.command('status').description('Check the active account'), [
    'hostname',
    'login',
    'scopes',
    'source',
  ]),
  async (_o, cmd) => {
    const options = opts(cmd),
      client = await clientFor(options),
      me = await client.me()
    const value = {
      hostname: client.origin,
      login: me.login,
      scopes: me.scopes,
      source: process.env.DEVOPS_TOKEN ? 'environment' : 'saved',
    }
    output(
      value,
      options,
      () => `${value.hostname}\n  Logged in as ${me.login}\n  Scopes: ${me.scopes.join(', ')}`,
    )
  },
)
act(
  auth
    .command('logout')
    .description('Remove the active local credential (does not revoke the server grant)'),
  async (_o, cmd) => {
    await logout(await currentHost(opts(cmd).hostname))
    console.error('Removed local credential. Revoke other grants in website account security.')
  },
)
act(
  auth.command('revoke').description('Revoke the saved server grant and remove it locally'),
  async (_o, cmd) => {
    const host = await currentHost(opts(cmd).hostname)
    await revoke(host)
    await logout(host)
    console.error('Authorization revoked.')
  },
)
act(auth.command('refresh').description('Rotate the active account token'), async (_o, cmd) => {
  await accessToken(await currentHost(opts(cmd).hostname), true)
  console.error('Credential refreshed.')
})
act(
  auth.command('switch').requiredOption('-u, --user <login>', 'Saved account login'),
  async (_o, cmd) => {
    await switchAccount(await currentHost(opts(cmd).hostname), opts(cmd).user)
  },
)
act(
  auth.command('token').description('Print the active access token; treat output as a secret'),
  async (_o, cmd) => {
    console.log(await accessToken(await currentHost(opts(cmd).hostname)))
  },
)
act(
  auth
    .command('setup-git')
    .description('Configure GCM OAuth endpoints for this host')
    .option('--desktop', 'Keep GitHub Desktop credential helper settings'),
  async (_o, cmd) => {
    const options = opts(cmd),
      host = await currentHost(options.hostname)
    if (!options.desktop) {
      runGit(['credential-manager', '--version'])
      runGit(['config', '--global', '--replace-all', `credential.${host}.helper`, ''])
      runGit(['config', '--global', '--add', `credential.${host}.helper`, 'manager'])
    }
    const settings: Record<string, string> = {
      provider: 'generic',
      oauthClientId: 'devops-gcm',
      oauthAuthorizeEndpoint: host + '/oauth/authorize',
      oauthTokenEndpoint: host + '/oauth/token',
      oauthRedirectUri: 'http://127.0.0.1',
      oauthScopes: 'git:read git:write',
      oauthDefaultUserName: 'oauth2',
      oauthUseClientAuthHeader: 'false',
    }
    for (const [key, value] of Object.entries(settings))
      runGit(['config', '--global', `credential.${host}.${key}`, value])
    console.error(
      `Configured GCM for ${host}. ${options.desktop ? 'Enable Use Git Credential Manager in GitHub Desktop → Advanced.' : 'Git will open the authorization page on next access.'}`,
    )
  },
)

const repo = program.command('repo').description('Manage repositories')
act(
  jsonOptions(
    repo
      .command('list')
      .option('-L, --limit <number>', 'Maximum repositories', positive, 30)
      .option('--search <text>', 'Name search'),
    repoFields,
  ),
  async (_o, cmd) => {
    const options = opts(cmd),
      client = await clientFor(options)
    const rows = await client.list<Repository>(
      '/api/repositories?q=' + encodeURIComponent(options.search || ''),
      options.limit,
    )
    output(
      rows.map((r) => repositoryJSON(r, client.origin)),
      options,
      () =>
        rows
          .map((r) => `${r.full_name}\t${r.description}\t${r.archived ? 'archived' : 'private'}`)
          .join('\n'),
    )
  },
)
act(
  jsonOptions(
    repo.command('view [repository]').option('-w, --web', 'Open repository in browser'),
    repoFields,
  ),
  async (name, _o, cmd) => {
    const options = opts(cmd),
      { client, repo } = await context(options, name),
      data = repositoryJSON(repo, client.origin)
    if (options.web) return openBrowser(data.url)
    output(data, options, () => `${data.nameWithOwner}\n${data.description}\n${data.url}`)
  },
)
act(
  repo
    .command('create <name>')
    .option('-d, --description <text>', 'Description', '')
    .option('--private', 'Repositories are private')
    .option('--clone', 'Clone after creation'),
  async (name, _o, cmd) => {
    const options = opts(cmd),
      client = await clientFor(options),
      created = await client.createRepository(name, options.description)
    console.log(client.origin + '/repositories/' + created.full_name)
    if (options.clone) runGit(['clone', '--', client.origin + created.clone_path])
  },
)
act(repo.command('clone <repository> [directory]'), async (name, directory, _o, cmd) => {
  const { client, repo } = await context(opts(cmd), name)
  runGit(['clone', '--', client.origin + repo.clone_path, ...(directory ? [directory] : [])])
})
act(
  repo.command('edit [repository]').requiredOption('-d, --description <text>', 'New description'),
  async (name, _o, cmd) => {
    const { client, repo } = await context(opts(cmd), name)
    await client.request(`/api/repositories/${repo.id}`, {
      method: 'PATCH',
      body: { description: opts(cmd).description },
    })
    console.error('Repository updated.')
  },
)
for (const operation of ['archive', 'unarchive', 'delete']) {
  act(
    repo
      .command(operation + ' [repository]')
      .option('--yes', 'Confirm this operation')
      .option('--request-code', 'Send an email verification code without changing the repository')
      .option('--code-stdin', 'Read the email verification code from standard input'),
    async (name, _o, cmd) => {
      const options = opts(cmd)
      if (!options.yes && !options.requestCode) throw new Error(`Use --yes to confirm repository ${operation}`)
      if (options.requestCode && options.codeStdin) throw new Error('Choose --request-code or --code-stdin')
      const { client, repo } = await context(options, name)
      const sendCode = () => client.request(`/api/repositories/${repo.id}/operation-code`, { method: 'POST' })
      if (options.requestCode) {
        await sendCode()
        console.error('Verification code sent to your account email. No repository changes were made.')
        return
      }
      let code = ''
      if (options.codeStdin) {
        for await (const chunk of process.stdin) {
          code += chunk
          if (code.length > 64) throw new Error('Expected a six-digit email verification code on stdin')
        }
        code = code.trim()
      } else if (process.stdin.isTTY) {
        await sendCode()
        const prompt = createInterface({ input: process.stdin, output: process.stderr })
        try { code = (await prompt.question('Email verification code: ')).trim() }
        finally { prompt.close() }
      } else {
        throw new Error('Request an email code with --request-code, then supply it using --code-stdin with --yes')
      }
      if (!/^[0-9]{6}$/.test(code)) throw new Error('Expected a six-digit email verification code')
      await client.request(
        `/api/repositories/${repo.id}${operation === 'delete' ? '' : '/archive'}`,
        {
          method: operation === 'delete' ? 'DELETE' : 'PUT',
          body:
            operation === 'delete'
              ? { confirm_name: repo.full_name, code }
              : { archived: operation === 'archive', code },
        },
      )
      console.error(`Repository ${operation === 'delete' ? 'soft-deleted' : operation + 'd'}.`)
    },
  )
}
act(
  repo
    .command('set-default <repository>')
    .description('Save the default repository in local Git config'),
  async (name, _o, cmd) => {
    const { client, repo } = await context(opts(cmd), name)
    runGit(['config', '--local', 'devops.repo', client.origin + repo.clone_path])
  },
)

for (const [noun, kind] of [
  ['issue', 'issue'],
  ['pr', 'pull'],
] as const) {
  const group = program
    .command(noun)
    .description(kind === 'issue' ? 'Manage issues' : 'Manage pull requests')
  const fields = kind === 'issue' ? itemFields : pullFields
  const pullInfo = async (
    client: DevOpsClient,
    repo: Repository,
    item: Item,
    options: Options,
  ): Promise<Pull | undefined> =>
    kind === 'pull' &&
    (!options.json || options.json.split(',').some((f: string) => !itemFields.includes(f)))
      ? client.pull(repo.id, item.number)
      : undefined
  act(
    jsonOptions(
      group
        .command('list')
        .option('-L, --limit <number>', 'Maximum items', positive, 30)
        .addOption(
          new Option('-s, --state <state>', 'Filter state')
            .choices(
              kind === 'pull' ? ['open', 'closed', 'merged', 'all'] : ['open', 'closed', 'all'],
            )
            .default('open'),
        )
        .option('-S, --search <text>', 'Search title and body')
        .option('-A, --author <login>', 'Author login or @me')
        .option('-a, --assignee <login>', 'Assignee login or @me')
        .option('-l, --label <name>', 'Label name'),
      fields,
    ),
    async (_o, cmd) => {
      const options = opts(cmd),
        { client, repo } = await context(options)
      const query = new URLSearchParams({ kind, state: options.state, q: options.search || '' })
      if (options.author || options.assignee) {
        const members = await client.request<any[]>(`/api/repositories/${repo.id}/members`)
        for (const key of ['author', 'assignee'])
          if (options[key]) {
            const login = options[key] === '@me' ? (await client.me()).login : options[key]
            const user = members.find((m) => m.username === login)
            if (!user) throw new Error(`Repository member not found: ${login}`)
            query.set(key, String(user.user_id))
          }
      }
      if (options.label) {
        const catalog = await client.request<Catalog[]>(`/api/repositories/${repo.id}/catalog`)
        const label = catalog.find((c) => c.kind === 'label' && c.name === options.label)
        if (!label) throw new Error('Label not found')
        query.set('label', String(label.id))
      }
      const rows = await client.list<Item>(
        `/api/repositories/${repo.id}/items?${query}`,
        options.limit,
      )
      const values: ReturnType<typeof itemJSON>[] = []
      for (let item of rows) {
        if (options.json?.split(',').includes('body'))
          item = await client.item(repo.id, item.number)
        values.push(itemJSON(item, client.origin, await pullInfo(client, repo, item, options)))
      }
      output(values, options, () =>
        values.map((v) => `#${v.number}\t${v.title}\t${v.state}\t${v.updatedAt}`).join('\n'),
      )
    },
  )
  act(
    jsonOptions(
      group
        .command('view <number-or-url>')
        .option('-w, --web', 'Open in browser')
        .option('-c, --comments', 'Show comments'),
      fields,
    ),
    async (value, _o, cmd) => {
      const options = opts(cmd),
        { client, repo, item } = await itemContext(kind, value, options),
        data = itemJSON(item, client.origin, await pullInfo(client, repo, item, options))
      if (options.web) return openBrowser(data.url)
      output(
        data,
        options,
        () =>
          `${item.title} #${item.number}\n${data.state} · @${data.author.login}\n\n${data.body}\n\n${data.url}`,
      )
      if (options.comments && !options.json) {
        const timeline = await client.list<any>(
          `/api/repositories/${repo.id}/items/${item.number}/timeline`,
          10000,
        )
        for (const event of timeline)
          if (event.action === 'commented')
            console.log(clean(`\n@${event.comment.author.username}\n${event.comment.body}`))
      }
    },
  )
  const create = metadataOptions(
    bodyOptions(group.command('create').requiredOption('-t, --title <text>', 'Title')),
  )
  if (kind === 'pull')
    create
      .option('-B, --base <branch>', 'Base branch', 'main')
      .option('-H, --head <branch>', 'Pushed head branch (default current)')
      .option('-d, --draft', 'Create a draft pull request')
  act(create, async (_o, cmd) => {
    const options = opts(cmd),
      { client, repo } = await context(options)
    const body = {
      title: options.title,
      body: (await inputBody(options)) || '',
      ...(await metadata(client, repo, options)),
    }
    const item =
      kind === 'pull'
        ? await client.createPull(repo.id, {
            ...body,
            base_ref: options.base,
            head_ref: options.head || git(['branch', '--show-current']),
            draft: !!options.draft,
          })
        : await client.createIssue(repo.id, body)
    console.log(new URL(item.url, client.origin).href)
  })
  act(
    metadataOptions(
      bodyOptions(group.command('edit <number-or-url>').option('-t, --title <text>', 'Title')),
    ),
    async (value, _o, cmd) => {
      const options = opts(cmd),
        { client, repo, item } = await itemContext(kind, value, options),
        meta = await metadata(client, repo, options)
      if (meta.catalog_ids) {
        const untouched = item.catalog.filter(
          (c) =>
            !(
              (options.label?.length && c.kind === 'label') ||
              (options.milestone && c.kind === 'milestone')
            ),
        )
        meta.catalog_ids.push(...untouched.map((c) => c.id))
      }
      const body = await inputBody(options)
      if (options.title === undefined && body === undefined && !Object.keys(meta).length)
        throw new Error('No changes specified')
      await client.updateItem(repo.id, item.number, {
        version: item.version,
        ...(options.title === undefined ? {} : { title: options.title }),
        ...(body === undefined ? {} : { body }),
        ...meta,
      })
      console.error(`Updated #${item.number}`)
    },
  )
  act(bodyOptions(group.command('comment <number-or-url>')), async (value, _o, cmd) => {
    const options = opts(cmd),
      body = await inputBody(options, true),
      { client, repo, item } = await itemContext(kind, value, options)
    const comment = await client.comment(repo.id, item.number, body!)
    console.log(new URL(comment.url, client.origin).href)
  })
  for (const action of ['close', 'reopen', 'lock', 'unlock']) {
    const command = group.command(action + ' <number-or-url>')
    if (action === 'close' && kind === 'issue')
      command.addOption(
        new Option('-r, --reason <reason>')
          .choices(['completed', 'not planned'])
          .default('completed'),
      )
    act(command, async (value, _o, cmd) => {
      const options = opts(cmd),
        { client, repo, item } = await itemContext(kind, value, options)
      await client.updateItem(repo.id, item.number, {
        version: item.version,
        ...(action === 'lock' || action === 'unlock'
          ? { locked: action === 'lock' }
          : {
              state: action === 'close' ? 'closed' : 'open',
              ...(options.reason ? { close_reason: options.reason.replace(' ', '_') } : {}),
            }),
      })
      console.error(`Updated #${item.number}`)
    })
  }
  if (kind === 'issue')
    act(
      group.command('delete <number-or-url>').option('--yes', 'Confirm deletion'),
      async (value, _o, cmd) => {
        if (!opts(cmd).yes) throw new Error('Use --yes to confirm deletion')
        const { client, repo, item } = await itemContext(kind, value, opts(cmd))
        await client.request(`/api/repositories/${repo.id}/items/${item.number}`, {
          method: 'DELETE',
        })
        console.error('Issue deleted.')
      },
    )
  if (kind !== 'pull') continue
  act(
    group.command('diff <number-or-url>').option('--name-only', 'Print changed file names'),
    async (value, _o, cmd) => {
      const { client, repo, item } = await itemContext(kind, value, opts(cmd))
      const endpoint = `/api/repositories/${repo.id}/pulls/${item.number}/diff`
      let snapshot = ''
      const chunks: string[] = []
      for (let page = 1; page <= 1000; page++) {
        const data = await client.request<any>(endpoint + '?page=' + page)
        const identity = data.base_sha + ':' + data.head_sha
        if (snapshot && identity !== snapshot)
          throw new Error('PR changed while reading diff; retry')
        snapshot = identity
        for (const file of data.files) {
          if (opts(cmd).nameOnly) chunks.push(file.path + '\n')
          else {
            const patch = await client.request<any>(
              endpoint + '?path=' + encodeURIComponent(file.path),
            )
            if (patch.base_sha + ':' + patch.head_sha !== snapshot)
              throw new Error('PR changed while reading diff; retry')
            chunks.push(patch.patch)
          }
        }
        if (page * data.page_size >= data.total) break
      }
      process.stdout.write(chunks.join(''))
    },
  )
  act(
    group.command('ready <number-or-url>').option('--undo', 'Mark as draft'),
    async (value, _o, cmd) => {
      const { client, repo, item } = await itemContext(kind, value, opts(cmd))
      await client.request(`/api/repositories/${repo.id}/pulls/${item.number}`, {
        method: 'PATCH',
        body: { version: item.version, draft: !!opts(cmd).undo },
      })
      console.error('Draft status updated.')
    },
  )
  act(
    bodyOptions(
      group
        .command('review <number-or-url>')
        .option('-a, --approve', 'Approve')
        .option('-r, --request-changes', 'Request changes')
        .option('-c, --comment', 'Comment review'),
    ),
    async (value, _o, cmd) => {
      const options = opts(cmd)
      if ([options.approve, options.requestChanges, options.comment].filter(Boolean).length !== 1)
        throw new Error('Choose --approve, --request-changes or --comment')
      const body = (await inputBody(options)) || '',
        { client, repo, item } = await itemContext(kind, value, options)
      const pull = await client.pull(repo.id, item.number)
      await client.request(`/api/repositories/${repo.id}/pulls/${item.number}/reviews`, {
        method: 'POST',
        body: {
          head_sha: pull.head_sha,
          state: options.approve
            ? 'approved'
            : options.requestChanges
              ? 'changes_requested'
              : 'commented',
          body,
        },
      })
      console.error('Review submitted.')
    },
  )
  act(
    jsonOptions(
      group
        .command('checks <number-or-url>')
        .option('--required', 'Only show required checks')
        .option('--watch', 'Watch checks until they finish')
        .option('--fail-fast', 'Stop watching on the first failed check')
        .option('-i, --interval <seconds>', 'Watch refresh interval', pollInterval, 10)
        .option('-w, --web', 'Open the pull request in the browser'),
      checkFields,
    ),
    async (value, _o, cmd) => {
      const options = opts(cmd)
      if (options.failFast && !options.watch)
        throw new Error('--fail-fast requires --watch')
      if (options.web && (options.watch || options.json))
        throw new Error('Choose --web or terminal output')
      const { client, repo, item } = await itemContext(kind, value, options)
      if (options.web) {
        openBrowser(new URL(item.url, client.origin).href)
        return
      }
      let previous = ''
      while (true) {
        const pull = await client.pull(repo.id, item.number)
        const checks = pull.checks
          .filter((c) => !options.required || pull.policy.required_checks.includes(c.name))
          .map(c => checkJSON(c, client.origin))
        const failed = checks.some((c) => ['fail', 'cancel'].includes(c.bucket))
        const pending = checks.some((c) => c.bucket === 'pending')
        const summary = clean(
          checks
            .map((c) => `${c.name}\t${c.bucket}\t${c.description}\t${c.link}`)
            .join('\n') || 'No checks reported.',
        )
        if (!options.watch || !pending || (options.failFast && failed)) {
          output(checks, options, () => summary)
          process.exitCode = failed || !checks.length ? 1 : pending ? 8 : 0
          break
        }
        if (summary !== previous) {
          console.error(summary)
          previous = summary
        }
        await sleep(options.interval * 1000)
      }
    },
  )
  act(
    group
      .command('checkout <number-or-url>')
      .option('-b, --branch <name>', 'New local branch name'),
    async (value, _o, cmd) => {
      const { client, repo, item } = await itemContext(kind, value, opts(cmd)),
        pull = await client.pull(repo.id, item.number)
      const branch = opts(cmd).branch || `pr-${item.number}`
      runGit(['check-ref-format', '--branch', branch])
      runGit(['fetch', '--', client.origin + repo.clone_path, 'refs/heads/' + pull.head_ref])
      runGit(['checkout', '-b', branch, 'FETCH_HEAD'])
    },
  )
  act(
    group
      .command('merge <number-or-url>')
      .option('-m, --merge', 'Merge commit (default)')
      .option('-s, --squash', 'Squash merge')
      .option('-r, --rebase', 'Rebase merge')
      .option('-d, --delete-branch', 'Delete the remote head branch after merging')
      .option('--match-head-commit <sha>', 'Refuse if the head differs')
      .option('--timeout <seconds>', 'Wait timeout', positive, 120),
    async (value, _o, cmd) => {
      const options = opts(cmd)
      if ([options.merge, options.squash, options.rebase].filter(Boolean).length > 1)
        throw new Error('Choose one merge method')
      const { client, repo, item } = await itemContext(kind, value, options),
        pull = await client.pull(repo.id, item.number)
      if (options.matchHeadCommit && options.matchHeadCommit !== pull.head_sha)
        throw new Error('Head commit changed; merge refused')
      const intent = await client.merge(repo.id, item.number, {
        base_sha: pull.base_sha,
        head_sha: pull.head_sha,
        method: options.squash ? 'squash' : options.rebase ? 'rebase' : 'merge',
        delete_branch: !!options.deleteBranch,
      })
      const deadline = Date.now() + options.timeout * 1000
      while (Date.now() < deadline) {
        const result = await client.mergeStatus(repo.id, item.number, intent.id)
        if (result.state === 'complete') {
          console.log(`Merged #${item.number}: ${result.result_sha}`)
          return
        }
        if (result.state === 'failed') throw new Error(`Merge failed: ${result.error}`)
        await sleep(1000)
      }
      console.error(
        `Merge task ${intent.id} is still pending. Inspect devops pr view ${item.number} or devops api /api/repositories/${repo.id}/pulls/${item.number}/merges/${intent.id}; do not assume success.`,
      )
      process.exitCode = 8
    },
  )
}
act(
  program
    .command('api <endpoint>')
    .description('Call a native /api endpoint (DevOps schemas)')
    .option('-X, --method <method>', 'HTTP method')
    .option('-f, --raw-field <key=value>', 'String field', append, [])
    .option('-F, --field <key=value>', 'JSON scalar field', append, [])
    .option('--input <file>', 'JSON body file (- for stdin)')
    .option('--paginate', 'Fetch every DevOps page')
    .option('--slurp', 'Output pages as one array')
    .option('-q, --jq <expression>', 'Filter with jq'),
  async (endpoint, _o, cmd) => {
    const options = opts(cmd),
      client = await clientFor(options)
    const path = endpoint.startsWith('/api/') ? endpoint : '/api/' + endpoint.replace(/^\//, '')
    const fields: Record<string, unknown> = {}
    for (const [values, typed] of [
      [options.rawField, false],
      [options.field, true],
    ] as const)
      for (const value of values) {
        const index = value.indexOf('=')
        if (index < 1) throw new Error('Fields use key=value')
        const raw = value.slice(index + 1)
        let parsed: unknown = raw
        if (typed) {
          try {
            parsed = JSON.parse(raw)
          } catch {}
        }
        fields[value.slice(0, index)] = parsed
      }
    if (options.input && Object.keys(fields).length)
      throw new Error('Choose --input or field options')
    const body = options.input
      ? JSON.parse((await inputBody({ bodyFile: options.input }))!)
      : Object.keys(fields).length
        ? fields
        : undefined
    const method = (options.method || (body !== undefined ? 'POST' : 'GET')).toUpperCase()
    const url = new URL(path, client.origin)
    if (method === 'GET' && body)
      for (const [key, value] of Object.entries(body)) url.searchParams.set(key, String(value))
    if (options.paginate) {
      if (method !== 'GET') throw new Error('--paginate requires GET')
      const pages = []
      for await (const page of client.pages(url.pathname + url.search)) {
        if (options.slurp) pages.push(page)
        else printJSON(page, options.jq)
      }
      if (options.slurp) printJSON(pages, options.jq)
    } else
      printJSON(
        await client.request(url.pathname + url.search, {
          method,
          body: method === 'GET' ? undefined : body,
        }),
        options.jq,
      )
  },
)

registerCI(program)

try {
  await program.parseAsync()
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  console.error(clean(message))
  process.exitCode =
    error instanceof OAuthError && error.code === 'access_denied'
      ? 2
      : (error instanceof APIError && error.status === 401) ||
          (error instanceof OAuthError && error.code === 'invalid_grant') ||
          /Authentication required|run devops auth login/i.test(message)
        ? 4
        : 1
}

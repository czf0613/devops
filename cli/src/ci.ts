import { Command, Option } from 'commander'
import { readFile, stat } from 'node:fs/promises'
import { createInterface } from 'node:readline/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import { context, parseRepo, positive, type Options } from './context.js'
import { openBrowser } from './auth.js'
import { clean, jsonOptions, output } from './output.js'
import { downloadArtifacts } from './ci-artifacts.js'
import {
  ciEvent,
  ciStatus,
  runFailed,
  runFields,
  runJSON,
  runSummary,
  runURL,
  runViewFields,
  workflowFields,
} from './ci-output.js'
import type {
  CIRun,
  CIRunDetail,
  CITrigger,
  DevOpsClient,
  Repository,
  Workflow,
} from './sdk.js'

const opts = (command: Command) => command.optsWithGlobals()
const append = (value: string, previous: string[]) => [...previous, value]
export function pollInterval(value: string) {
  const seconds = positive(value)
  if (seconds > 86400)
    throw new Error('Polling interval must be between 1 and 86400 seconds')
  return seconds
}
type Context = { client: DevOpsClient; repo: Repository }
async function choose<T>(rows: T[], label: (row: T) => string, noun: string): Promise<T> {
  if (!rows.length) throw new Error(`No ${noun}s found`)
  if (!process.stdin.isTTY)
    throw new Error(`Specify a ${noun}; interactive selection requires a terminal`)
  const terminal = createInterface({ input: process.stdin, output: process.stderr })
  try {
    rows.forEach((row, index) => console.error(clean(`${index + 1}. ${label(row)}`)))
    const number = positive(await terminal.question(`Select ${noun} number: `))
    if (number > rows.length) throw new Error('Selection is out of range')
    return rows[number - 1]!
  } finally {
    terminal.close()
  }
}
function workflowURL(ctx: Context, path: string) {
  return `${ctx.client.origin}/repositories/${ctx.repo.full_name}/ci?${new URLSearchParams({ workflow: path })}`
}
async function selectWorkflow(
  ctx: Context,
  value: string | undefined,
  ref: string,
  manual = false,
): Promise<Workflow> {
  const catalog = await ctx.client.workflows(ctx.repo.id, ref)
  const rows = manual ? catalog.items.filter((w) => w.manual) : catalog.items
  if (!value) return choose(rows, (row) => `${row.name} (${row.path})`, 'workflow')
  const exact = catalog.items.find((w) => w.path === value)
  const matches = exact
    ? [exact]
    : catalog.items.filter((w) => w.path.split('/').at(-1) === value || w.name === value)
  if (!matches.length)
    throw new Error(`Workflow not found: ${value}; use its name or file path`)
  if (matches.length !== 1)
    throw new Error('Ambiguous workflow name; specify its full file path')
  const result = matches[0]!
  if (manual && result.error)
    throw new Error(`Workflow configuration error: ${result.error}`)
  if (manual && !result.manual)
    throw new Error('Workflow does not support workflow_dispatch')
  return result
}
async function inputText(file: string): Promise<string> {
  const limit = 128 * 1024
  if (file !== '-') {
    if ((await stat(file)).size > limit) throw new Error('Workflow input exceeds 128 KiB')
    const content = await readFile(file, 'utf8')
    if (Buffer.byteLength(content) > limit)
      throw new Error('Workflow input exceeds 128 KiB')
    return content
  }
  let result = '',
    bytes = 0
  for await (const chunk of process.stdin) {
    bytes += Buffer.byteLength(chunk)
    if (bytes > limit) throw new Error('Workflow input exceeds 128 KiB')
    result += chunk
  }
  return result
}
async function workflowInputs(
  workflow: Workflow,
  options: Options,
): Promise<NonNullable<CITrigger['inputs']>> {
  const values: Record<string, unknown> = Object.create(null)
  if (options.json && (options.field.length || options.rawField.length))
    throw new Error('Choose --json input or -f/-F fields')
  let stdinUsed = false
  if (options.json) {
    const parsed = JSON.parse(await inputText('-'))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
      throw new Error('Workflow inputs must be a JSON object')
    Object.assign(values, parsed)
  }
  for (const [fields, expand] of [
    [options.rawField, false],
    [options.field, true],
  ] as const) {
    for (const field of fields) {
      const equals = field.indexOf('=')
      if (equals < 1) throw new Error('Workflow fields use key=value')
      const key = field.slice(0, equals),
        value = field.slice(equals + 1)
      if (Object.hasOwn(values, key)) throw new Error(`Duplicate workflow input: ${key}`)
      if (expand && value.startsWith('@')) {
        if (value === '@-' && stdinUsed) throw new Error('Only one field can read stdin')
        stdinUsed ||= value === '@-'
        values[key] = await inputText(value.slice(1))
      } else values[key] = value
    }
  }
  if (
    process.stdin.isTTY &&
    !options.json &&
    !options.field.length &&
    !options.rawField.length
  ) {
    const terminal = createInterface({ input: process.stdin, output: process.stderr })
    try {
      for (const [key, definition] of Object.entries(workflow.inputs)) {
        const hint = definition.options ? ` (${definition.options.join(', ')})` : ''
        const fallback = definition.default === undefined ? '' : ` [${definition.default}]`
        const answer = await terminal.question(
          clean(`${definition.description || key}${hint}${fallback}: `),
        )
        if (answer !== '') values[key] = answer
      }
    } finally {
      terminal.close()
    }
  }
  for (const [key, value] of Object.entries(values)) {
    if (!Object.hasOwn(workflow.inputs, key))
      throw new Error(`Unknown workflow input: ${key}`)
    const definition = workflow.inputs[key]!
    if (definition.type === 'boolean') {
      if (value === 'true' || value === 'false') values[key] = value === 'true'
      else if (typeof value !== 'boolean')
        throw new Error(`Input ${key} requires true or false`)
    } else if (definition.type === 'number') {
      if (
        (typeof value !== 'number' && typeof value !== 'string') ||
        String(value).trim() === '' ||
        !Number.isFinite(Number(value))
      )
        throw new Error(`Input ${key} requires a finite number`)
      values[key] = Number(value)
    } else if (typeof value !== 'string') throw new Error(`Input ${key} requires a string`)
    if (definition.type === 'choice' && !definition.options?.includes(String(values[key])))
      throw new Error(`Input ${key} must be one of: ${definition.options?.join(', ')}`)
  }
  for (const [key, definition] of Object.entries(workflow.inputs)) {
    const value =
      values[key] ?? definition.default ?? (definition.type === 'boolean' ? false : '')
    if (definition.required && value === '')
      throw new Error(`Required workflow input missing: ${key}`)
  }
  return values as NonNullable<CITrigger['inputs']>
}

function runSelection(value: string, options: Options) {
  if (!value.includes('://')) return positive(value)
  const url = new URL(value)
  const match = /^\/repositories\/([^/]+)\/([^/]+)\/ci\/(\d+)$/.exec(url.pathname)
  if (!match || url.username || url.password || url.search || url.hash)
    throw new Error('Invalid CI run URL')
  const repository = `${url.origin}/repositories/${match[1]}/${match[2]}`
  const actual = parseRepo(repository)
  if (options.repo) {
    const explicit = parseRepo(options.repo)
    if (
      explicit.owner !== actual.owner ||
      explicit.name !== actual.name ||
      (explicit.host && explicit.host !== actual.host)
    )
      throw new Error('Run URL conflicts with --repo')
  }
  options.repo = repository
  return positive(match[3]!)
}
async function runContext(value: string | undefined, options: Options) {
  const id = value ? runSelection(value, options) : undefined
  const ctx = await context(options)
  const selected =
    id ??
    (
      await choose(
        await ctx.client.runs(ctx.repo.id),
        (row) => `#${row.id} ${row.name} ${row.ref} ${row.state}`,
        'run',
      )
    ).id
  return { ...ctx, run: await ctx.client.run(ctx.repo.id, selected) }
}
async function workflowFilter(ctx: Context, value: string | undefined) {
  if (!value) return ''
  // Full paths can also select historical runs after a workflow file was removed.
  if (value.startsWith('.github/workflows/') || value === '.devops/ci.yml') return value
  return (await selectWorkflow(ctx, value, ctx.repo.default_branch)).path
}
async function printLogs(ctx: Context, run: CIRunDetail) {
  for (const job of run.jobs) {
    const attempt = job.attempts.at(-1)
    if (!attempt) {
      console.error(clean(`No execution logs yet: ${job.name}`))
      continue
    }
    let after = 0
    console.error(
      clean(
        `--- ${job.display_name || job.name} (job ${job.id}, attempt ${attempt.number}) ---`,
      ),
    )
    // A single view drains available pages; watch is a separate operation.
    for (let page = 0; page < 10000; page++) {
      const logs = await ctx.client.runLogs(ctx.repo.id, attempt.id, after)
      if (
        !Number.isSafeInteger(logs.next) ||
        logs.next < after ||
        logs.chunks.some((c) => c.seq <= after || c.seq > logs.next)
      )
        throw new Error('Invalid CI log cursor')
      for (const chunk of logs.chunks) process.stdout.write(clean(chunk.text))
      if (logs.complete || logs.next === after) break
      after = logs.next
      if (page === 9999) throw new Error('CI log pagination limit exceeded')
    }
  }
}

export function registerCI(program: Command) {
  const workflow = program
    .command('workflow')
    .description('List, view and run CI workflows (gh workflow conventions)')
  jsonOptions(
    workflow
      .command('list')
      .alias('ls')
      .description('List workflow files')
      .option('-a, --all', 'Include workflows when repository CI is disabled')
      .option('-L, --limit <number>', 'Maximum workflows', positive, 50),
    workflowFields,
  ).action(async (_o, cmd) => {
    const options = opts(cmd),
      ctx = await context(options)
    const [catalog, config] = await Promise.all([
      ctx.client.workflows(ctx.repo.id, ctx.repo.default_branch),
      ctx.client.ciConfig(ctx.repo.id),
    ])
    const rows = (config.enabled || options.all ? catalog.items : [])
      .slice(0, options.limit)
      .map((w) => ({
        name: w.name,
        path: w.path,
        state: config.enabled ? 'active' : 'disabled_manually',
      }))
    output(
      rows,
      options,
      () =>
        rows.map((w) => `${w.name}\t${w.state}\t${w.path}`).join('\n') ||
        'No workflows found.',
    )
  })
  workflow
    .command('view [workflow]')
    .description('View a workflow by name or file path')
    .option('-r, --ref <branch>', 'Branch containing the workflow')
    .option('-y, --yaml', 'Print the workflow source')
    .option('-w, --web', 'Open workflow in the browser')
    .action(async (value, _o, cmd) => {
      const options = opts(cmd)
      if (options.yaml && options.web) throw new Error('Choose --yaml or --web')
      const ctx = await context(options),
        ref = options.ref ?? ctx.repo.default_branch
      const selected = await selectWorkflow(ctx, value, ref)
      if (options.yaml) {
        const source = await ctx.client.workflowSource(ctx.repo.id, selected.path, ref)
        if (source.type !== 'blob' || source.content === null)
          throw new Error('Workflow source is unavailable')
        process.stdout.write(source.content)
      } else if (options.web) openBrowser(workflowURL(ctx, selected.path))
      else {
        console.log(
          clean(
            `${selected.name}\n${selected.path}\nBranch: ${ref}\nManual trigger: ${selected.manual ? 'workflow_dispatch' : 'unavailable'}${selected.error ? `\nConfiguration error: ${selected.error}` : ''}`,
          ),
        )
        const runs = await ctx.client.runs(ctx.repo.id, {
          workflow: selected.path,
          limit: 3,
        })
        for (const run of runs)
          console.log(
            clean(
              `${run.id}\t${run.state}\t${run.ref}\t${runURL(run, ctx.repo, ctx.client.origin)}`,
            ),
          )
      }
    })
  workflow
    .command('run [workflow]')
    .description('Trigger workflow_dispatch with inputs')
    .option(
      '-r, --ref <branch>',
      'Branch containing the workflow (default: repository default branch)',
    )
    .option('-f, --raw-field <key=value>', 'String input; may be repeated', append, [])
    .option(
      '-F, --field <key=value>',
      'Input supporting @file and @- for stdin',
      append,
      [],
    )
    .option('--json', 'Read workflow inputs as JSON from stdin')
    .action(async (value, _o, cmd) => {
      const options = opts(cmd),
        ctx = await context(options),
        ref = options.ref ?? ctx.repo.default_branch
      const selected = await selectWorkflow(ctx, value, ref, true)
      const inputs = await workflowInputs(selected, options)
      const run = await ctx.client.runWorkflow(ctx.repo.id, {
        ref,
        workflow: selected.path,
        inputs,
      })
      console.log(runURL(run, ctx.repo, ctx.client.origin))
      if (runFailed(run)) {
        console.error(clean(run.error || 'Workflow could not start'))
        process.exitCode = 1
      }
    })

  const runs = program
    .command('run')
    .description('Inspect and manage CI workflow runs (gh run conventions)')
  jsonOptions(
    runs
      .command('list')
      .alias('ls')
      .description('List recent runs')
      .option('-L, --limit <number>', 'Maximum matching runs', positive, 20)
      .option('-w, --workflow <workflow>', 'Filter by workflow name or file path')
      .option('-b, --branch <branch>', 'Filter by branch')
      .option('-c, --commit <sha>', 'Filter by full commit SHA')
      .addOption(
        new Option('-e, --event <event>', 'Filter by trigger').choices([
          'push',
          'pull_request',
          'workflow_dispatch',
        ]),
      )
      .addOption(
        new Option('-s, --status <status>', 'Filter by run status or conclusion').choices([
          'queued',
          'in_progress',
          'completed',
          'success',
          'failure',
          'cancelled',
          'timed_out',
          'skipped',
        ]),
      ),
    runFields,
  ).action(async (_o, cmd) => {
    const options = opts(cmd)
    if (options.commit && !/^[a-f0-9]{40}$/i.test(options.commit))
      throw new Error('--commit requires a full 40-character SHA')
    const ctx = await context(options),
      path = await workflowFilter(ctx, options.workflow)
    const query = new URLSearchParams(path ? { workflow: path } : {})
    const result: CIRun[] = []
    for await (const page of ctx.client.pages<CIRun>(
      `/api/repositories/${ctx.repo.id}/ci/runs?${query}`,
    )) {
      for (const run of page.items) {
        const status = ciStatus(run.state)
        if (options.branch && options.branch !== run.ref) continue
        if (options.commit && options.commit.toLowerCase() !== run.sha) continue
        if (options.event && options.event !== ciEvent(run.event)) continue
        if (options.status && ![status.status, status.conclusion].includes(options.status))
          continue
        result.push(run)
        if (result.length >= options.limit) break
      }
      if (result.length >= options.limit) break
    }
    output(
      result.map((r) => runJSON(r, ctx.repo, ctx.client.origin)),
      options,
      () =>
        result
          .map((r) => {
            const state = ciStatus(r.state)
            return `${state.status}\t${state.conclusion}\t${r.name}\t${r.ref}\t${ciEvent(r.event)}\t${r.id}\t${r.created_at}`
          })
          .join('\n') || 'No workflow runs found.',
    )
  })
  jsonOptions(
    runs
      .command('view [run-id]')
      .description('View a run or job; accepts a DevOps run URL')
      .option('-j, --job <id>', 'View one job within the selected run', positive)
      .option('--log', 'Print available logs for the run or job')
      .option('-v, --verbose', 'Show job steps')
      .option('-w, --web', 'Open the run in the browser')
      .option('--exit-status', 'Exit non-zero if the completed run failed'),
    runViewFields,
  ).action(async (value, _o, cmd) => {
    const options = opts(cmd)
    if ([options.log, options.web, !!options.json].filter(Boolean).length > 1)
      throw new Error('Choose --log, --web or --json')
    const ctx = await runContext(value, options)
    const run = { ...ctx.run }
    if (options.job) {
      const job = run.jobs.find((j) => j.id === options.job)
      if (!job) throw new Error('Job does not belong to this run')
      run.jobs = [job]
    }
    if (options.web) openBrowser(runURL(run, ctx.repo, ctx.client.origin))
    else if (options.log) await printLogs(ctx, run)
    else
      output(
        runJSON(run, ctx.repo, ctx.client.origin),
        options,
        () =>
          `${runSummary(run, options.verbose)}\n${runURL(run, ctx.repo, ctx.client.origin)}`,
      )
    if (options.exitStatus && runFailed(run)) process.exitCode = 1
  })
  runs
    .command('watch [run-id]')
    .description('Watch until completion; Ctrl-C stops watching only')
    .option('-i, --interval <seconds>', 'Refresh interval', pollInterval, 3)
    .option('--compact', 'Only display active or unsuccessful steps')
    .option('--exit-status', 'Exit non-zero if the run failed')
    .action(async (value, _o, cmd) => {
      const options = opts(cmd),
        ctx = await runContext(value, options)
      let run = ctx.run,
        previous = ''
      while (true) {
        const summary = clean(runSummary(run, true, options.compact))
        if (summary !== previous) {
          console.error(summary)
          previous = summary
        }
        if (ciStatus(run.state).status === 'completed') break
        await sleep(options.interval * 1000)
        run = await ctx.client.run(ctx.repo.id, run.id)
      }
      if (options.exitStatus && runFailed(run)) process.exitCode = 1
    })
  runs
    .command('cancel [run-id]')
    .description('Request cancellation of a run')
    .action(async (value, _o, cmd) => {
      const ctx = await runContext(value, opts(cmd))
      await ctx.client.cancelRun(ctx.repo.id, ctx.run.id)
      console.error(
        `Cancellation requested for run ${ctx.run.id}. Use devops run watch ${ctx.run.id} to observe completion.`,
      )
    })
  runs
    .command('rerun [run-id]')
    .description('Rerun the entire original commit and workflow snapshot')
    .action(async (value, _o, cmd) => {
      const ctx = await runContext(value, opts(cmd))
      const run = await ctx.client.rerun(ctx.repo.id, ctx.run.id)
      console.log(runURL(run, ctx.repo, ctx.client.origin))
      if (runFailed(run)) {
        console.error(clean(run.error || 'Workflow could not restart'))
        process.exitCode = 1
      }
    })
  runs
    .command('download <run-id>')
    .description('Download and verify artifacts from a run')
    .option('-D, --dir <directory>', 'Destination directory', '.')
    .option('-n, --name <name>', 'Select artifact group by name (repeatable)', append, [])
    .option(
      '-p, --pattern <glob>',
      'Select artifact groups with * or ? (repeatable)',
      append,
      [],
    )
    .action(async (value, _o, cmd) => {
      const options = opts(cmd),
        ctx = await runContext(value, options)
      const count = await downloadArtifacts(ctx.client, ctx.repo.id, ctx.run, {
        dir: options.dir,
        name: options.name,
        pattern: options.pattern,
      })
      console.error(`Downloaded and verified ${count} artifact file(s).`)
    })
}

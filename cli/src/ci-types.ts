/** Native DevOps CI responses. CLI projections are separate from these SDK types. */
export interface CIConfig {
  enabled: boolean
  pools: string[]
  available_pools: string[]
  can_configure: boolean
  can_run: boolean
}
export interface WorkflowInput {
  description?: string
  type?: 'string' | 'boolean' | 'number' | 'choice'
  required?: boolean
  default?: string | boolean | number
  options?: string[]
}
export interface Workflow {
  path: string
  name: string
  manual: boolean
  inputs: Record<string, WorkflowInput>
  error: string
}
export interface WorkflowCatalog {
  items: Workflow[]
  ref: string
  sha: string
}
export type CIState =
  | 'queued'
  | 'assigned'
  | 'starting'
  | 'running'
  | 'success'
  | 'failure'
  | 'cancelled'
  | 'timed_out'
  | 'lost'
  | 'skipped'
export interface CIArtifact {
  id: number
  name: string
  size: number
  sha256: string
  expires_at: string
}
export interface CIStep {
  name: string
  state: CIState | 'pending'
  exit_code?: number | null
}
export interface CIAttempt {
  id: number
  number: number
  worker: string
  state: CIState
  cancel_requested: boolean
  steps: CIStep[]
  error: string
  created_at: string
  finished_at: string | null
  artifacts: CIArtifact[]
}
export interface CIJob {
  id: number
  name: string
  display_name: string
  state: CIState
  environment: Record<string, unknown>
  needs: string[]
  attempts: CIAttempt[]
}
export interface CIRun {
  id: number
  repository_id: number
  event: string
  ref: string
  sha: string
  state: CIState
  error: string
  created_at: string
  name: string
  workflow_path: string
  rerun_of: number | null
  item_id: number | null
}
export interface CIRunDetail extends CIRun {
  jobs: CIJob[]
}
export interface CITrigger {
  ref: string
  workflow?: string
  inputs?: Record<string, string | boolean | number>
}
export interface CILogs {
  chunks: { seq: number; text: string }[]
  next: number
  complete: boolean
}

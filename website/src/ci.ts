export interface CIConfig { enabled: boolean; pools: string[]; available_pools: string[]; can_configure: boolean; can_run: boolean }
export interface CIArtifact { id: number; name: string; size: number; sha256: string; expires_at: string }
export interface CIAttempt { id: number; number: number; worker: string; state: string; cancel_requested: boolean; steps: { name: string; state: string; exit_code: number | null }[]; error: string; created_at: string; finished_at: string | null; artifacts: CIArtifact[] }
export interface CIJob { id: number; name: string; display_name?: string; state: string; environment: { os: string | null; arch: string | null; pool: string; executor: string; constraints?: { field: string; operator: string; value: string }[] }; needs: string[]; attempts: CIAttempt[] }
export interface CIRun { id: number; name: string; workflow_path: string; event: string; ref: string; sha: string; state: string; error: string; created_at: string; rerun_of: number | null; jobs: CIJob[] }
export interface CIWorkflow { path: string; name: string; manual: boolean; error: string; inputs: Record<string, { description?: string; type?: string; required?: boolean; default?: string | number | boolean; options?: string[] }> }
export interface CICapabilities { os?: string; arch?: string; version?: string; runtime?: number; labels?: string[]; os_version?: string; os_edition?: string; kernel_version?: string; distribution?: { id?: string; name?: string; version?: string }; libc?: { name?: string; version?: string } }
export interface CIWorker { id: number; name: string; pool: string; enabled: boolean; draining: boolean; registered: boolean; online: boolean; last_seen_at: string | null; capabilities: CICapabilities; registration_token?: string }
export const eventLabel = (value: string) => ({ manual: '手动运行', push: '推送', pull_request: 'Pull request' }[value] ?? value)
export function environmentLabel(caps: CICapabilities) {
  if (!caps.os) return '平台待上报'
  const name = caps.os === 'macos' ? 'macOS' : caps.os === 'linux' ? caps.distribution?.name || 'Linux' : caps.os_edition || 'Windows'
  return [name, caps.os_version || '版本待上报', caps.arch].filter(Boolean).join(' · ')
}
export const stateLabel = (value: string) => ({ queued: '排队中', assigned: '等待接收', starting: '准备中', running: '执行中', success: '成功', failure: '失败', cancelled: '已取消', timed_out: '超时', lost: '执行失联', skipped: '已跳过' }[value] ?? value)
export const isActive = (value: string) => ['queued', 'assigned', 'starting', 'running'].includes(value)
export const stateTone = (value: string): 'green' | 'red' | undefined => value === 'success' ? 'green' : ['failure', 'lost', 'timed_out'].includes(value) ? 'red' : undefined

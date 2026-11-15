export interface Namespace {
  id: number
  name: string
  repository_id: number
  enabled: boolean
  version: number
  repository_name?: string
}
export interface RegistryOverview {
  enabled: boolean
  host: string
  username: string
  items: Namespace[]
}
export interface RegistryImage { name: string; reference: string }
export interface RegistryTag {
  tag: string
  digest?: string
  missing?: boolean
  media_type?: string
  size_bytes?: number
  platforms?: string[]
  attestation_count?: number
}
export interface RegistryPage<T> { total: number; page: number; page_size: number; items: T[] }
export interface RegistryCredential {
  id: number
  name: string
  access: 'read' | 'write'
  expires_at: string | null
  last_used_at: string | null
}
export interface AdminNamespace extends Namespace {
  bound: boolean
  deleted_at?: string | null
  purged_at?: string | null
  repository: { id: number; full_name: string; archived: boolean; deleted: boolean }
}
export interface RegistryRepository { id: number; full_name: string }

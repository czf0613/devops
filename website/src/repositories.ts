export interface MemberSettings {
  access: 'read' | 'write'
  role: string
  is_admin?: boolean
}

export interface Repository {
  id: number
  name: string
  description: string
  archived: boolean
  protect_main: boolean
  protect_push: boolean
  full_name: string
  clone_path: string
  default_branch: string
  owner: { id: number; username: string; display_name: string }
  member_count: number
  created_at: string
  updated_at: string
  permissions: {
    is_owner: boolean
    is_admin: boolean
    can_manage_members: boolean
    access: 'read' | 'write'
    role: string
  }
}

export interface RepositoryMember extends MemberSettings {
  user_id: number
  username: string
  display_name: string
  status: 'active' | 'disabled'
  is_owner: boolean
  is_admin: boolean
}

export interface MemberCandidate {
  id: number
  username: string
  display_name: string
}

export interface AdminRepository {
  id: number
  name: string
  full_name: string
  owner: { id: number; username: string; display_name: string }
  archived: boolean
  deleted_at: string | null
  created_at: string
  updated_at: string
  member_count: number
}
export interface AdminRepositoryDetail extends AdminRepository { members: RepositoryMember[] }

export function repositoryUrl(repo: Pick<Repository, 'owner' | 'name'>): string {
  return `/repositories/${encodeURIComponent(repo.owner.username)}/${encodeURIComponent(repo.name)}`
}

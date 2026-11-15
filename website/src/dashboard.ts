import type { ItemSummary, Person } from './collaboration'
import { dateText, type Page } from './api'

export interface DashboardRepository {
  id: number
  full_name: string
  url: string
  archived: boolean
}
export interface DashboardItem extends ItemSummary {
  repository: DashboardRepository
  author: Person
  updated_at: string
  draft: boolean
}
export interface DashboardEvent {
  id: number
  action: string
  actor: Person | null
  created_at: string
  item: DashboardItem
}
export interface Notification {
  id: number
  item: ItemSummary
  action: string
  actor: Person | null
  url: string
  created_at: string
  read_at: string | null
}
export type Notifications = Page<Notification> & { unread_count: number }
export const activityLabels: Record<string, string> = {
  created: '创建了', commented: '评论了', reviewed: '审阅了', merged: '合并了',
  closed: '关闭了', reopened: '重新打开了', assigned: '分配了',
  review_requested: '请求审阅', pull_updated: '更新了', mentioned: '提及了你',
}
export function relativeTime(value: string, now = Date.now()) {
  const minutes = Math.max(0, Math.floor((now - new Date(value).getTime()) / 60000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`
  if (minutes < 43200) return `${Math.floor(minutes / 1440)} 天前`
  return dateText(value)
}

import { daysBetween } from './inactivity'
import type { Member } from './types'

export function lastActivityText(member: Member, asOf: string): string {
  if (member.status === 'pending') return '—'
  if (member.lastActivity === undefined) return '데이터 없음'
  if (member.lastActivity === null) return '없음'
  const days = daysBetween(member.lastActivity, asOf)
  return `${member.lastActivity} (${days <= 0 ? '오늘' : `${days}일 전`})`
}

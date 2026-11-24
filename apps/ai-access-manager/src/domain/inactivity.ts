import type { Member, Verdict } from './types'

/** 권한을 받은 지 이 일수 안이면 아직 써 볼 시간이 없었다고 보고 정리 대상에서 뺀다. */
export const GRACE_DAYS = 14

const DAY = 24 * 60 * 60 * 1000

export function daysBetween(fromIso: string, toIso: string): number {
  const from = Date.parse(`${fromIso.slice(0, 10)}T00:00:00Z`)
  const to = Date.parse(`${toIso.slice(0, 10)}T00:00:00Z`)
  return Math.floor((to - from) / DAY)
}

export function todayIso(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10)
}

/**
 * 한 사람의 사용 상태를 판정한다. Copilot Manager(PowerShell)의 규칙과 같다:
 * 값이 없으면 추측하지 않고 '판정 불가', 받은 지 얼마 안 됐으면 '신규'.
 */
export function judge(member: Member, inactiveDays: number, asOf: string): Verdict {
  if (member.status === 'pending') return '초대 대기'
  if (member.addedAt && daysBetween(member.addedAt, asOf) < GRACE_DAYS) return '신규'
  if (member.lastActivity === undefined) return '판정 불가'
  if (member.lastActivity === null) return '사용 이력 없음'
  return daysBetween(member.lastActivity, asOf) >= inactiveDays ? '미사용' : '사용 중'
}

/** 사람이 확인하고 회수할 만한 대상 */
export function isCandidate(verdict: Verdict): boolean {
  return verdict === '미사용' || verdict === '사용 이력 없음'
}

export function countActive(members: Member[], days: number, asOf: string): number {
  return members.filter(
    (m) => m.status === 'active' && typeof m.lastActivity === 'string' && daysBetween(m.lastActivity, asOf) < days,
  ).length
}

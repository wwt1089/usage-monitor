import type { ActionResult, Capabilities, Member, ServiceAdapter, ServiceSnapshot } from '../../domain/types'
import { normalizeEmail } from '../../domain/people'
import { call, errorText, findService, isoDay, list, rec, text } from '../generated'

/**
 * Cursor 연결. 커넥터 정의: connectors/cursor-admin/
 * 사용 API: Admin API(GET /teams/members, POST /teams/spend, POST /teams/daily-usage-data, POST /teams/remove-member).
 * 응답 필드 이름은 공식 문서를 직접 확인하지 못해 여러 후보를 함께 읽는다. 실제 응답으로 한 번 대조해야 한다.
 */

const OPERATIONS = ['GetTeamMembers']
const DAY_MS = 86_400_000
/** daily-usage-data 는 한 번에 30일까지만 받는다. */
const CHUNK_DAYS = 30
const CHUNKS = 3

export const CURSOR_CAPABILITIES: Capabilities = {
  canGrant: false,
  canRevoke: true,
  grantLabel: '초대',
  revokeLabel: '팀에서 제거',
  notes: [
    '멤버 추가는 Admin API 에 없습니다. Cursor 대시보드나 SCIM 으로 추가합니다.',
    '팀에서 제거는 Enterprise 플랜에서만 동작합니다.',
    '마지막 사용일은 최근 90일의 일별 사용 기록에서 계산합니다. 구매 좌석 수는 API 가 알려 주지 않습니다.',
  ],
}

function isActiveRow(row: Record<string, unknown>): boolean {
  if (typeof row.isActive === 'boolean') return row.isActive
  return Object.entries(row).some(([key, value]) => key !== 'date' && key !== 'day' && typeof value === 'number' && value > 0)
}

export function createCursorAdapter(): ServiceAdapter | null {
  const service = findService(/cursor/i, OPERATIONS)
  if (!service) return null
  const canRemove = typeof service.RemoveMember === 'function'

  async function loadLastActivity(warnings: string[]): Promise<Map<string, string> | null> {
    if (typeof service!.GetDailyUsageData !== 'function') return null
    try {
      const byEmail = new Map<string, string>()
      const now = Date.now()
      for (let chunk = 0; chunk < CHUNKS; chunk += 1) {
        const endDate = now - chunk * CHUNK_DAYS * DAY_MS
        const startDate = endDate - CHUNK_DAYS * DAY_MS
        const body = await call(service!, 'GetDailyUsageData', { startDate, endDate })
        for (const row of list(body, 'data', 'dailyUsage')) {
          const email = normalizeEmail(text(row, 'email', 'userEmail'))
          const day = isoDay(row.date ?? row.day)
          if (!email || !day || !isActiveRow(row)) continue
          if ((byEmail.get(email) ?? '') < day) byEmail.set(email, day)
        }
      }
      return byEmail
    } catch (error) {
      warnings.push(`일별 사용 기록을 읽지 못했습니다: ${errorText(error)}`)
      return null
    }
  }

  async function loadSpend(): Promise<Map<string, string>> {
    const byEmail = new Map<string, string>()
    if (typeof service!.GetSpend !== 'function') return byEmail
    try {
      const body = await call(service!, 'GetSpend', {})
      for (const row of list(body, 'teamMemberSpend', 'data')) {
        const cents = row.overallSpendCents ?? row.spendCents
        const email = normalizeEmail(text(row, 'email', 'userEmail'))
        if (email && typeof cents === 'number') byEmail.set(email, `이번 주기 $${(cents / 100).toFixed(2)}`)
      }
    } catch {
      // 지출은 부가 정보라 실패해도 목록은 보여 준다.
    }
    return byEmail
  }

  return {
    id: 'cursor',
    mode: 'live',
    capabilities: { ...CURSOR_CAPABILITIES, canRevoke: canRemove },
    async load(): Promise<ServiceSnapshot> {
      const warnings: string[] = []
      const body = await call(service, 'GetTeamMembers')
      const usage = await loadLastActivity(warnings)
      const spend = await loadSpend()
      const members: Member[] = list(body, 'teamMembers', 'members', 'data')
        .filter((row) => rec(row).isRemoved !== true)
        .map((row) => {
          const email = normalizeEmail(text(row, 'email'))
          const role = text(row, 'role') || 'member'
          const owner = /owner/i.test(role)
          return {
            id: text(row, 'id', 'userId') || email,
            email,
            name: text(row, 'name'),
            role,
            status: 'active',
            lastActivity: usage ? (usage.get(email) ?? null) : undefined,
            detail: spend.get(email),
            revocable: canRemove && !owner,
            revokeBlockedReason: owner
              ? '소유자는 이 앱에서 제거하지 않습니다.'
              : canRemove
                ? undefined
                : '커넥터에 제거 동작이 없습니다.',
          }
        })
      return { members, seats: { total: null, used: members.length, pending: 0 }, warnings }
    },
    async grant(): Promise<ActionResult> {
      return { ok: false, message: 'Cursor 는 API 로 멤버를 추가할 수 없습니다. 대시보드나 SCIM 을 사용하세요.' }
    },
    async revoke(member): Promise<ActionResult> {
      try {
        await call(service, 'RemoveMember', { email: member.email })
        return { ok: true, message: '팀에서 제거했습니다.' }
      } catch (error) {
        return { ok: false, message: errorText(error) }
      }
    },
  }
}

import type { ActionResult, Capabilities, Member, ServiceAdapter, ServiceSnapshot } from '../../domain/types'
import { normalizeEmail } from '../../domain/people'
import { call, dayOffset, errorText, findService, isoDay, list, rec, text, type GeneratedService } from '../generated'

/**
 * Claude Enterprise 연결. 커넥터 정의: connectors/claude-admin/
 * 사용 API: Admin API(/v1/organizations/users, /invites)와 Analytics API(/v1/organizations/analytics/*).
 * 키는 claude.ai 조직 설정에서 Primary owner 가 만들고 범위는 read:members, write:members, read:analytics 가 필요하다.
 */

const OPERATIONS = ['ListUsers', 'ListInvites', 'CreateInvite', 'DeleteUser', 'DeleteInvite']
const ADMIN_ROLES = new Set(['owner', 'primary_owner', 'membership_admin'])
const PAGE = 1000
const USAGE_WINDOW_DAYS = 90

export const CLAUDE_CAPABILITIES: Capabilities = {
  canGrant: true,
  canRevoke: true,
  grantLabel: '초대',
  revokeLabel: '제거',
  notes: [
    '초대는 좌석을 하나 차지하며, 남은 좌석이 없으면 실패합니다.',
    'SCIM·JIT 로 사용자를 관리하는 조직은 초대와 제거가 API 에서 거절됩니다. 이 경우 IdP(Entra ID)에서 처리합니다.',
    '소유자 등 관리 역할은 이 앱에서 제거하지 않습니다.',
    '마지막 사용일은 최근 90일 기준이며 하루 늦게 반영됩니다.',
  ],
}

async function pageAll(service: GeneratedService, operation: string): Promise<Record<string, unknown>[]> {
  const rows: Record<string, unknown>[] = []
  let after: string | undefined
  for (let guard = 0; guard < 50; guard += 1) {
    const page = await call(service, operation, PAGE, after)
    rows.push(...list(page, 'data'))
    const lastId = text(page, 'last_id')
    if (rec(page).has_more !== true || !lastId) break
    after = lastId
  }
  return rows
}

export function createClaudeAdapter(): ServiceAdapter | null {
  const service = findService(/claude|anthropic/i, OPERATIONS)
  if (!service) return null

  async function loadLastActivity(warnings: string[]): Promise<{ byEmail: Map<string, string>; asOf: string } | null> {
    if (typeof service!.ListUserActivity !== 'function') {
      warnings.push('커넥터에 사용 현황 동작(ListUserActivity)이 없어 마지막 사용일을 표시하지 않습니다.')
      return null
    }
    // 사용 데이터는 다음 날 13:30 UTC 쯤 올라온다. 아직 없는 날짜를 요청하면 400 이므로 이틀 전까지만 본다.
    const ending = dayOffset(-1)
    const starting = dayOffset(-1 - USAGE_WINDOW_DAYS)
    try {
      const byEmail = new Map<string, string>()
      let page: string | undefined
      for (let guard = 0; guard < 50; guard += 1) {
        const body = await call(service!, 'ListUserActivity', starting, ending, PAGE, page)
        for (const row of list(body, 'data')) {
          const email = normalizeEmail(text(row.user, 'email_address', 'email'))
          const last = isoDay(row.last_activity_date)
          if (email && last) byEmail.set(email, last)
        }
        page = text(body, 'next_page') || undefined
        if (!page) break
      }
      return { byEmail, asOf: dayOffset(-2) }
    } catch (error) {
      warnings.push(`사용 현황을 읽지 못했습니다(키에 read:analytics 범위가 있는지 확인): ${errorText(error)}`)
      return null
    }
  }

  async function loadSeatTotal(): Promise<number | null> {
    if (typeof service!.ListActivitySummaries !== 'function') return null
    try {
      const body = await call(service!, 'ListActivitySummaries', dayOffset(-5))
      const days = list(body, 'data')
      for (let i = days.length - 1; i >= 0; i -= 1) {
        const count = days[i].assigned_seat_count
        if (typeof count === 'number') return count
      }
    } catch {
      // 좌석 수는 부가 정보라 실패해도 목록은 보여 준다.
    }
    return null
  }

  return {
    id: 'claude',
    mode: 'live',
    capabilities: CLAUDE_CAPABILITIES,
    async load(): Promise<ServiceSnapshot> {
      const warnings: string[] = []
      const [users, invites] = await Promise.all([pageAll(service, 'ListUsers'), pageAll(service, 'ListInvites')])
      const usage = await loadLastActivity(warnings)
      const seatTotal = await loadSeatTotal()

      const members: Member[] = users.map((user) => {
        const email = normalizeEmail(text(user, 'email'))
        const role = text(user, 'role') || 'user'
        const admin = ADMIN_ROLES.has(role)
        return {
          id: text(user, 'id'),
          email,
          name: text(user, 'name'),
          role,
          status: 'active',
          addedAt: isoDay(user.added_at) || undefined,
          lastActivity: usage ? (usage.byEmail.get(email) ?? null) : undefined,
          revocable: !admin,
          revokeBlockedReason: admin ? '관리 역할은 claude.ai 관리 화면에서 처리합니다.' : undefined,
        }
      })
      for (const invite of invites) {
        if (text(invite, 'status') !== 'pending') continue
        members.push({
          id: text(invite, 'id'),
          email: normalizeEmail(text(invite, 'email')),
          name: '',
          role: text(invite, 'role') || 'user',
          status: 'pending',
          addedAt: isoDay(invite.invited_at) || undefined,
          lastActivity: null,
          detail: '초대 대기',
          revocable: true,
        })
      }
      const pending = members.filter((m) => m.status === 'pending').length
      return {
        members,
        seats: { total: seatTotal, used: members.length - pending, pending },
        asOf: usage?.asOf,
        warnings,
      }
    },
    async grant(email): Promise<ActionResult> {
      try {
        await call(service, 'CreateInvite', { email, role: 'user' })
        return { ok: true, message: '초대를 보냈습니다.' }
      } catch (error) {
        return { ok: false, message: errorText(error) }
      }
    },
    async revoke(member): Promise<ActionResult> {
      try {
        if (member.status === 'pending') {
          await call(service, 'DeleteInvite', member.id)
          return { ok: true, message: '초대를 취소했습니다.' }
        }
        await call(service, 'DeleteUser', member.id)
        return { ok: true, message: '조직에서 제거했습니다.' }
      } catch (error) {
        return { ok: false, message: errorText(error) }
      }
    },
  }
}

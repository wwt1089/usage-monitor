import type { ActionResult, Capabilities, Member, ServiceAdapter, ServiceSnapshot } from '../../domain/types'
import { config } from '../../config'
import { normalizeEmail } from '../../domain/people'
import { call, findService, isoDay, list, rec, text } from '../generated'

/**
 * ChatGPT Enterprise/Edu 연결. 커넥터 정의: connectors/chatgpt-admin/
 * 확인된 주소는 사용자 목록(GET /v1/manage/workspaces/{workspace_id}/users)뿐이다.
 * 초대·제거·사용량 주소는 Admin API reference 를 확인한 뒤 커넥터와 이 파일에 추가한다.
 */

const OPERATIONS = ['ListWorkspaceUsers']

export const CHATGPT_CAPABILITIES: Capabilities = {
  canGrant: false,
  canRevoke: false,
  grantLabel: '초대',
  revokeLabel: '제거',
  notes: [
    '현재는 조회만 연결되어 있습니다. 초대·제거 API 주소를 확인한 뒤 추가할 예정입니다.',
    'SCIM 으로 관리하는 사용자는 IdP(Entra ID)에서 제거해야 합니다.',
    '워크스페이스 Admin 키는 Enterprise·Edu 에서 소유자나 관리자가 만듭니다.',
  ],
}

export function createChatgptAdapter(): ServiceAdapter | null {
  const service = findService(/chatgpt|openai/i, OPERATIONS)
  if (!service) return null

  return {
    id: 'chatgpt',
    mode: 'live',
    capabilities: CHATGPT_CAPABILITIES,
    async load(): Promise<ServiceSnapshot> {
      if (!config.chatgptWorkspaceId) {
        throw new Error('.env.local 에 VITE_CHATGPT_WORKSPACE_ID 를 넣고 다시 빌드해야 합니다.')
      }
      const rows: Record<string, unknown>[] = []
      let after: string | undefined
      for (let guard = 0; guard < 50; guard += 1) {
        const body = await call(service, 'ListWorkspaceUsers', config.chatgptWorkspaceId, 200, after)
        const page = list(body, 'data', 'users', 'members')
        rows.push(...page)
        const next = text(body, 'last_id', 'next', 'next_page')
        if (rec(body).has_more !== true || !next || page.length === 0) break
        after = next
      }
      const members: Member[] = rows.map((row) => {
        const user = rec(row.user)
        const email = normalizeEmail(text(row, 'email') || text(user, 'email'))
        const last = isoDay(row.last_active_at ?? row.last_activity_at ?? row.last_active)
        return {
          id: text(row, 'id', 'user_id') || email,
          email,
          name: text(row, 'name') || text(user, 'name'),
          role: text(row, 'role') || 'member',
          status: text(row, 'status') === 'pending' ? 'pending' : 'active',
          addedAt: isoDay(row.created_at ?? row.added_at ?? row.created) || undefined,
          lastActivity: last || undefined,
          revocable: false,
          revokeBlockedReason: '제거 API 가 아직 연결되지 않았습니다.',
        }
      })
      const pending = members.filter((m) => m.status === 'pending').length
      return {
        members,
        seats: { total: null, used: members.length - pending, pending },
        warnings: members.some((m) => m.lastActivity !== undefined)
          ? []
          : ['사용자 목록 응답에 마지막 사용일이 없어 미사용 여부를 판정하지 않습니다.'],
      }
    },
    async grant(): Promise<ActionResult> {
      return { ok: false, message: 'ChatGPT 초대는 아직 연결되지 않았습니다.' }
    },
    async revoke(): Promise<ActionResult> {
      return { ok: false, message: 'ChatGPT 제거는 아직 연결되지 않았습니다.' }
    },
  }
}

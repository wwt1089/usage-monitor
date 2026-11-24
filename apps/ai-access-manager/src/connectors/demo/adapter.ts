import type { ActionResult, Capabilities, Member, ServiceAdapter, ServiceId, ServiceSnapshot } from '../../domain/types'
import { todayIso } from '../../domain/inactivity'
import { normalizeEmail } from '../../domain/people'
import { buildDemoMembers, DEMO_TOTAL_SEATS } from './data'

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** 커넥터가 아직 없을 때 쓰는 예시 데이터 연결. 브라우저 메모리 안에서만 바뀐다. */
export function createDemoAdapter(id: ServiceId, capabilities: Capabilities): ServiceAdapter {
  let members: Member[] = buildDemoMembers(id)

  return {
    id,
    mode: 'demo',
    capabilities,
    async load(): Promise<ServiceSnapshot> {
      await wait(150)
      return {
        members: members.map((m) => ({ ...m })),
        seats: {
          total: DEMO_TOTAL_SEATS[id],
          used: members.filter((m) => m.status === 'active').length,
          pending: members.filter((m) => m.status === 'pending').length,
        },
        asOf: todayIso(),
        warnings: [],
      }
    },
    async grant(email): Promise<ActionResult> {
      await wait(150)
      const key = normalizeEmail(email)
      if (members.some((m) => normalizeEmail(m.email) === key)) {
        return { ok: false, message: '이미 권한이 있는 사용자입니다.' }
      }
      const total = DEMO_TOTAL_SEATS[id]
      if (total !== null && members.length >= total) {
        return { ok: false, message: '남은 좌석이 없습니다.' }
      }
      const invited = id !== 'm365'
      members = [
        ...members,
        {
          id: `${id}-${key}`,
          email: key,
          name: '',
          role: '사용자',
          status: invited ? 'pending' : 'active',
          addedAt: todayIso(),
          lastActivity: null,
          revocable: true,
        },
      ]
      return { ok: true, message: invited ? '초대를 보냈습니다.' : '라이선스를 부여했습니다.' }
    },
    async revoke(member): Promise<ActionResult> {
      await wait(150)
      if (!members.some((m) => m.id === member.id)) return { ok: false, message: '대상을 찾지 못했습니다.' }
      members = members.filter((m) => m.id !== member.id)
      return { ok: true, message: '회수했습니다.' }
    },
  }
}

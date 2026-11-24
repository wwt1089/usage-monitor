import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { GeneratedService, OperationResult } from '../generated'

/**
 * 실제 연결부를 가짜 커넥터로 검증한다. 가짜 응답은 각 서비스 문서의 응답 모양을 따른다.
 * 실제 서비스나 실제 generated/ 코드는 쓰지 않는다.
 */

let fake: GeneratedService = {}

vi.mock('../generated', async (original) => ({
  ...(await original<typeof import('../generated')>()),
  findService: (_pattern: RegExp, operations: string[]) =>
    operations.every((name) => typeof fake[name] === 'function') ? fake : null,
}))

const ok = (data: unknown): Promise<OperationResult> => Promise.resolve({ success: true, data })
const fail = (message: string): Promise<OperationResult> => Promise.resolve({ success: false, error: { message } })

const { createClaudeAdapter } = await import('./claude')
const { createCursorAdapter } = await import('./cursor')
const { createChatgptAdapter } = await import('./chatgpt')
const { createM365Adapter } = await import('./m365')

beforeEach(() => {
  fake = {}
})

describe('Claude', () => {
  function service(overrides: GeneratedService = {}): GeneratedService {
    return {
      ListUsers: vi.fn((_limit, after) =>
        after === undefined
          ? ok({
              data: [
                { id: 'user_1', email: 'Owner@contoso.com', name: '소유자', role: 'owner', added_at: '2026-01-05T00:00:00Z' },
                { id: 'user_2', email: 'a@contoso.com', name: 'A', role: 'user', added_at: '2026-02-01T00:00:00Z' },
              ],
              has_more: true,
              last_id: 'user_2',
            })
          : ok({ data: [{ id: 'user_3', email: 'b@contoso.com', name: 'B', role: 'user' }], has_more: false, last_id: 'user_3' }),
      ),
      ListInvites: vi.fn(() =>
        ok({
          data: [
            { id: 'invite_1', email: 'new@contoso.com', role: 'user', status: 'pending', invited_at: '2026-11-20T01:00:00Z' },
            { id: 'invite_0', email: 'old@contoso.com', role: 'user', status: 'accepted' },
          ],
          has_more: false,
        }),
      ),
      ListUserActivity: vi.fn(() =>
        ok({ data: [{ user: { email_address: 'a@contoso.com' }, last_activity_date: '2026-11-01' }], next_page: null }),
      ),
      ListActivitySummaries: vi.fn(() => ok({ data: [{ assigned_seat_count: 8 }, { assigned_seat_count: null }] })),
      CreateInvite: vi.fn(() => ok({ id: 'invite_2' })),
      DeleteUser: vi.fn(() => ok({ type: 'user_deleted' })),
      DeleteInvite: vi.fn(() => ok({})),
      ...overrides,
    }
  }

  it('여러 쪽의 멤버와 대기 중 초대를 합치고 마지막 사용일을 붙인다', async () => {
    fake = service()
    const snapshot = await createClaudeAdapter()!.load()
    expect(fake.ListUsers).toHaveBeenCalledTimes(2)
    expect(snapshot.members.map((m) => [m.email, m.status])).toEqual([
      ['owner@contoso.com', 'active'],
      ['a@contoso.com', 'active'],
      ['b@contoso.com', 'active'],
      ['new@contoso.com', 'pending'],
    ])
    expect(snapshot.members[1].lastActivity).toBe('2026-11-01')
    expect(snapshot.members[2].lastActivity).toBeNull()
    expect(snapshot.seats).toEqual({ total: 8, used: 3, pending: 1 })
  })

  it('관리 역할은 회수 대상에서 막는다', async () => {
    fake = service()
    const [owner, user] = (await createClaudeAdapter()!.load()).members
    expect(owner.revocable).toBe(false)
    expect(user.revocable).toBe(true)
  })

  it('사용 현황을 못 읽으면 추측하지 않고 판정 불가로 두며 이유를 알린다', async () => {
    fake = service({ ListUserActivity: vi.fn(() => fail('forbidden')) })
    const snapshot = await createClaudeAdapter()!.load()
    expect(snapshot.members[1].lastActivity).toBeUndefined()
    expect(snapshot.warnings.join(' ')).toContain('forbidden')
  })

  it('초대·제거·초대 취소를 맞는 동작으로 보낸다', async () => {
    fake = service()
    const adapter = createClaudeAdapter()!
    const { members } = await adapter.load()
    expect(await adapter.grant('x@contoso.com')).toMatchObject({ ok: true })
    expect(fake.CreateInvite).toHaveBeenCalledWith({ email: 'x@contoso.com', role: 'user' })
    await adapter.revoke(members[1])
    expect(fake.DeleteUser).toHaveBeenCalledWith('user_2')
    await adapter.revoke(members[3])
    expect(fake.DeleteInvite).toHaveBeenCalledWith('invite_1')
  })

  it('서비스가 거절하면 그 사유를 그대로 돌려준다', async () => {
    fake = service({ DeleteUser: vi.fn(() => fail('SCIM manages membership')) })
    const adapter = createClaudeAdapter()!
    const { members } = await adapter.load()
    expect(await adapter.revoke(members[1])).toEqual({ ok: false, message: 'SCIM manages membership' })
  })

  it('커넥터가 없으면 null 이라 예시 데이터로 넘어간다', () => {
    expect(createClaudeAdapter()).toBeNull()
  })
})

describe('Cursor', () => {
  const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`)

  function service(overrides: GeneratedService = {}): GeneratedService {
    return {
      GetTeamMembers: vi.fn(() =>
        ok({
          teamMembers: [
            { name: 'A', email: 'a@contoso.com', role: 'member' },
            { name: 'O', email: 'o@contoso.com', role: 'owner' },
            { name: 'Gone', email: 'gone@contoso.com', role: 'member', isRemoved: true },
          ],
        }),
      ),
      GetDailyUsageData: vi.fn(() =>
        ok({
          data: [
            { email: 'a@contoso.com', date: day('2026-11-02'), isActive: true },
            { email: 'a@contoso.com', date: day('2026-11-10'), isActive: true },
            { email: 'a@contoso.com', date: day('2026-11-20'), isActive: false },
          ],
        }),
      ),
      GetSpend: vi.fn(() => ok({ teamMemberSpend: [{ email: 'a@contoso.com', spendCents: 1234 }] })),
      RemoveMember: vi.fn(() => ok({})),
      ...overrides,
    }
  }

  it('제거된 멤버를 빼고, 실제로 쓴 날 중 가장 최근을 마지막 사용일로 본다', async () => {
    fake = service()
    const snapshot = await createCursorAdapter()!.load()
    expect(snapshot.members.map((m) => m.email)).toEqual(['a@contoso.com', 'o@contoso.com'])
    expect(snapshot.members[0].lastActivity).toBe('2026-11-10')
    expect(snapshot.members[0].detail).toBe('이번 주기 $12.34')
    expect(snapshot.members[1].lastActivity).toBeNull()
    expect(snapshot.members[1].revocable).toBe(false)
    expect(fake.GetDailyUsageData).toHaveBeenCalledTimes(3)
  })

  it('이메일로 제거를 요청한다', async () => {
    fake = service()
    const adapter = createCursorAdapter()!
    const { members } = await adapter.load()
    await adapter.revoke(members[0])
    expect(fake.RemoveMember).toHaveBeenCalledWith({ email: 'a@contoso.com' })
  })

  it('제거 동작이 정책으로 빠져 있으면 회수를 막는다', async () => {
    fake = service()
    delete fake.RemoveMember
    const adapter = createCursorAdapter()!
    expect(adapter.capabilities.canRevoke).toBe(false)
    expect((await adapter.load()).members[0].revocable).toBe(false)
  })
})

describe('ChatGPT', () => {
  it('조회만 하고 부여·회수는 막는다', async () => {
    vi.stubEnv('VITE_CHATGPT_WORKSPACE_ID', 'ws_1')
    vi.resetModules()
    fake = { ListWorkspaceUsers: vi.fn(() => ok({ data: [{ id: 'u1', email: 'A@contoso.com', name: 'A', role: 'member' }] })) }
    const adapter = (await import('./chatgpt')).createChatgptAdapter()!
    const snapshot = await adapter.load()
    expect(fake.ListWorkspaceUsers).toHaveBeenCalledWith('ws_1', 200, undefined)
    expect(snapshot.members[0]).toMatchObject({ email: 'a@contoso.com', revocable: false, lastActivity: undefined })
    expect(adapter.capabilities).toMatchObject({ canGrant: false, canRevoke: false })
    vi.unstubAllEnvs()
  })

  it('워크스페이스 ID 가 없으면 무엇을 해야 하는지 알린다', async () => {
    fake = { ListWorkspaceUsers: vi.fn(() => ok({ data: [] })) }
    await expect(createChatgptAdapter()!.load()).rejects.toThrow('VITE_CHATGPT_WORKSPACE_ID')
  })
})

describe('Microsoft 365 Copilot', () => {
  const SKU = 'sku-copilot'

  function service(overrides: GeneratedService = {}): GeneratedService {
    return {
      ListSubscribedSkus: vi.fn(() =>
        ok({
          value: [
            { skuId: SKU, skuPartNumber: 'Microsoft_365_Copilot', prepaidUnits: { enabled: 10 }, servicePlans: [{ servicePlanName: 'M365_COPILOT_APPS' }] },
            { skuId: 'sku-e3', skuPartNumber: 'SPE_E3', prepaidUnits: { enabled: 50 }, servicePlans: [{ servicePlanName: 'EXCHANGE_S_ENTERPRISE' }] },
            { skuId: 'sku-studio', skuPartNumber: 'COPILOT_STUDIO_VIRAL_TRIAL', prepaidUnits: { enabled: 9 }, servicePlans: [{ servicePlanName: 'CDS_VIRAL' }] },
          ],
        }),
      ),
      ListUsers: vi.fn(() =>
        ok({
          value: [
            { id: 'u1', displayName: '직접', userPrincipalName: 'Direct@contoso.com', licenseAssignmentStates: [{ skuId: SKU, state: 'Active', assignedByGroup: null, lastUpdatedDateTime: '2026-03-01T00:00:00Z' }] },
            { id: 'u2', displayName: '그룹', userPrincipalName: 'group@contoso.com', licenseAssignmentStates: [{ skuId: SKU, state: 'Active', assignedByGroup: 'g1' }] },
            { id: 'u3', displayName: '둘다', userPrincipalName: 'mixed@contoso.com', licenseAssignmentStates: [{ skuId: SKU, state: 'Active', assignedByGroup: null }, { skuId: SKU, state: 'Active', assignedByGroup: 'g1' }] },
            { id: 'u4', displayName: '오류그룹', userPrincipalName: 'err@contoso.com', licenseAssignmentStates: [{ skuId: SKU, state: 'Active', assignedByGroup: null }, { skuId: SKU, state: 'Error', assignedByGroup: 'g1' }] },
          ],
        }),
      ),
      GetUser: vi.fn(() => ok({ id: 'u9', userPrincipalName: 'new@contoso.com', usageLocation: 'KR' })),
      AssignLicense: vi.fn(() => ok({})),
      GetCopilotUsageUserDetail: vi.fn(() =>
        ok({
          value: [
            { userPrincipalName: 'direct@contoso.com', lastActivityDate: '2026-11-18', reportRefreshDate: '2026-11-22' },
            { userPrincipalName: 'group@contoso.com', lastActivityDate: null, reportRefreshDate: '2026-11-22' },
          ],
        }),
      ),
      ...overrides,
    }
  }

  it('Copilot SKU 만 골라 좌석과 보유자를 읽는다', async () => {
    fake = service()
    const snapshot = await createM365Adapter()!.load()
    expect(fake.ListUsers).toHaveBeenCalledTimes(1)
    expect(fake.ListUsers).toHaveBeenCalledWith(`assignedLicenses/any(x:x/skuId eq ${SKU})`, expect.any(String), 999)
    expect(snapshot.seats).toEqual({ total: 10, used: 4, pending: 0 })
    expect(snapshot.asOf).toBe('2026-11-22')
  })

  it('할당 방식을 구분하고 오류 상태의 할당은 세지 않는다', async () => {
    fake = service()
    const { members } = await createM365Adapter()!.load()
    expect(members.map((m) => [m.detail, m.revocable])).toEqual([
      ['직접 할당', true],
      ['그룹 상속', false],
      ['직접 + 그룹', false],
      ['직접 할당', true],
    ])
  })

  it('리포트의 값은 그대로, 리포트에 없는 사람은 판정 불가로 둔다', async () => {
    fake = service()
    const { members } = await createM365Adapter()!.load()
    expect(members.map((m) => m.lastActivity)).toEqual(['2026-11-18', null, undefined, undefined])
  })

  it('리포트의 사용자 이름이 숨겨져 있으면 아무도 판정하지 않는다', async () => {
    fake = service({
      GetCopilotUsageUserDetail: vi.fn(() => ok({ value: [{ userPrincipalName: 'A1B2C3D4', lastActivityDate: '2026-11-18' }] })),
    })
    const snapshot = await createM365Adapter()!.load()
    expect(snapshot.members.every((m) => m.lastActivity === undefined)).toBe(true)
    expect(snapshot.warnings.join(' ')).toContain('숨겨져')
  })

  it('부여와 회수는 직접 할당만 바꾼다', async () => {
    fake = service()
    const adapter = createM365Adapter()!
    const { members } = await adapter.load()
    expect(await adapter.grant('new@contoso.com')).toMatchObject({ ok: true })
    expect(fake.AssignLicense).toHaveBeenLastCalledWith('u9', { addLicenses: [{ skuId: SKU, disabledPlans: [] }], removeLicenses: [] })
    await adapter.revoke(members[0])
    expect(fake.AssignLicense).toHaveBeenLastCalledWith('u1', { addLicenses: [], removeLicenses: [SKU] })
  })

  it('사용 위치가 없으면 부여하지 않는다', async () => {
    fake = service({ GetUser: vi.fn(() => ok({ id: 'u9', usageLocation: null })) })
    const result = await createM365Adapter()!.grant('new@contoso.com')
    expect(result.ok).toBe(false)
    expect(fake.AssignLicense).not.toHaveBeenCalled()
  })

  it('Copilot SKU 가 여러 개면 부여할 SKU 를 정하라고 알린다', async () => {
    fake = service({
      ListSubscribedSkus: vi.fn(() =>
        ok({
          value: ['A', 'B'].map((n) => ({ skuId: n, skuPartNumber: `COPILOT_${n}`, prepaidUnits: { enabled: 1 }, servicePlans: [{ servicePlanName: 'M365_COPILOT_X' }] })),
        }),
      ),
    })
    const result = await createM365Adapter()!.grant('new@contoso.com')
    expect(result).toMatchObject({ ok: false })
    expect(result.message).toContain('2개')
    expect(fake.AssignLicense).not.toHaveBeenCalled()
  })
})

import type { ActionResult, Capabilities, Member, ServiceAdapter, ServiceSnapshot } from '../../domain/types'
import { config } from '../../config'
import { normalizeEmail } from '../../domain/people'
import { call, errorText, findService, isoDay, list, rec, text } from '../generated'

/**
 * Microsoft 365 Copilot 연결. 커넥터 정의: connectors/m365-graph/ (Microsoft Graph, 로그인한 관리자 권한으로 호출)
 * 판정 규칙은 Copilot Manager(PowerShell)와 맞춘다:
 *  - Copilot SKU = 이름에 COPILOT 이 있고 M365_COPILOT_* 서비스 플랜을 가진 SKU 전부
 *  - 할당 방식은 오류 상태를 뺀 licenseAssignmentStates 로 판정 (직접 / 그룹 / 직접+그룹)
 *  - 사용 리포트에 UPN 이 숨겨져 있으면 추측하지 않고 판정 불가
 */

const OPERATIONS = ['ListSubscribedSkus', 'ListUsers', 'GetUser', 'AssignLicense']
const USER_FIELDS = 'id,displayName,userPrincipalName,licenseAssignmentStates'
const PAGE = 999

export const M365_CAPABILITIES: Capabilities = {
  canGrant: true,
  canRevoke: true,
  grantLabel: '라이선스 부여',
  revokeLabel: '라이선스 회수',
  notes: [
    '직접 할당한 라이선스만 이 화면에서 회수합니다. 그룹에서 상속받은 라이선스는 그룹에서 제외해야 합니다.',
    '부여하려면 사용자에게 사용 위치(usageLocation)가 설정되어 있어야 합니다.',
    '사용 리포트에서 사용자 이름이 숨겨져 있으면 마지막 사용일을 판정할 수 없습니다(관리 센터 > 조직 설정 > 보고서).',
    '로그인한 관리자의 Entra 역할(License Administrator, Reports Reader 등)이 그대로 적용됩니다.',
  ],
}

interface Sku {
  id: string
  name: string
  total: number
}

function isCopilotSku(sku: Record<string, unknown>): boolean {
  const plans = list(sku.servicePlans)
  return /COPILOT/i.test(text(sku, 'skuPartNumber')) && plans.some((p) => /^M365_COPILOT/i.test(text(p, 'servicePlanName')))
}

export function createM365Adapter(): ServiceAdapter | null {
  const service = findService(/graph|m365|copilot|license/i, OPERATIONS)
  if (!service) return null

  async function copilotSkus(): Promise<Sku[]> {
    const body = await call(service!, 'ListSubscribedSkus')
    return list(body, 'value')
      .filter(isCopilotSku)
      .map((sku) => ({
        id: text(sku, 'skuId'),
        name: text(sku, 'skuPartNumber'),
        total: Number(rec(sku.prepaidUnits).enabled ?? 0),
      }))
  }

  async function lastActivityByUpn(warnings: string[]): Promise<{ byUpn: Map<string, string | null>; asOf?: string } | null> {
    if (typeof service!.GetCopilotUsageUserDetail !== 'function') return null
    try {
      const body = await call(service!, 'GetCopilotUsageUserDetail', 'D180', 'application/json')
      const rows = list(body, 'value')
      if (rows.length > 0 && rows.every((row) => !text(row, 'userPrincipalName').includes('@'))) {
        warnings.push('사용 리포트의 사용자 이름이 숨겨져 있어 마지막 사용일을 판정하지 않습니다.')
        return null
      }
      const byUpn = new Map<string, string | null>()
      let asOf: string | undefined
      for (const row of rows) {
        const upn = normalizeEmail(text(row, 'userPrincipalName'))
        if (!upn) continue
        byUpn.set(upn, isoDay(row.lastActivityDate) || null)
        asOf = asOf ?? (isoDay(row.reportRefreshDate) || undefined)
      }
      return { byUpn, asOf }
    } catch (error) {
      warnings.push(`사용 리포트를 읽지 못했습니다(Reports Reader 역할과 Reports.Read.All 권한 확인): ${errorText(error)}`)
      return null
    }
  }

  return {
    id: 'm365',
    mode: 'live',
    capabilities: M365_CAPABILITIES,
    async load(): Promise<ServiceSnapshot> {
      const warnings: string[] = []
      const skus = await copilotSkus()
      if (skus.length === 0) warnings.push('이 조직에서 Copilot SKU 를 찾지 못했습니다.')
      const usage = await lastActivityByUpn(warnings)
      const members: Member[] = []

      for (const sku of skus) {
        const body = await call(service, 'ListUsers', `assignedLicenses/any(x:x/skuId eq ${sku.id})`, USER_FIELDS, PAGE)
        if (text(body, '@odata.nextLink')) warnings.push(`${sku.name}: 보유자가 ${PAGE}명을 넘어 일부만 표시합니다.`)
        for (const user of list(body, 'value')) {
          const states = list(user.licenseAssignmentStates).filter(
            (s) => text(s, 'skuId') === sku.id && !/^(Error|Disabled)$/i.test(text(s, 'state')),
          )
          const direct = states.some((s) => !text(s, 'assignedByGroup'))
          const group = states.some((s) => Boolean(text(s, 'assignedByGroup')))
          const how = direct && group ? '직접 + 그룹' : group ? '그룹 상속' : '직접 할당'
          const upn = normalizeEmail(text(user, 'userPrincipalName'))
          const assigned = states.map((s) => isoDay(s.lastUpdatedDateTime)).filter(Boolean).sort()[0]
          members.push({
            id: `${text(user, 'id')}|${sku.id}`,
            email: upn,
            name: text(user, 'displayName'),
            role: '사용자',
            status: 'active',
            addedAt: assigned || undefined,
            // 리포트는 읽었는데 이 사람 행이 없으면 추측하지 않는다(판정 불가).
            lastActivity: usage && usage.byUpn.has(upn) ? usage.byUpn.get(upn) : undefined,
            detail: skus.length > 1 ? `${how} · ${sku.name}` : how,
            revocable: direct && !group,
            revokeBlockedReason: !group
              ? undefined
              : direct
                ? '직접 할당을 회수해도 그룹 상속이 남습니다. 그룹에서도 제외해야 합니다.'
                : '그룹에서 상속받은 라이선스입니다. 그룹에서 제외해야 회수됩니다.',
          })
        }
      }
      return {
        members,
        seats: { total: skus.reduce((sum, sku) => sum + sku.total, 0), used: members.length, pending: 0 },
        asOf: usage?.asOf,
        warnings,
      }
    },
    async grant(email): Promise<ActionResult> {
      try {
        const skus = await copilotSkus()
        const wanted = config.m365GrantSku.toLowerCase()
        const sku = wanted ? skus.find((s) => s.name.toLowerCase() === wanted) : skus.length === 1 ? skus[0] : undefined
        if (!sku) {
          return {
            ok: false,
            message: wanted
              ? `설정한 SKU(${config.m365GrantSku})를 찾지 못했습니다.`
              : `Copilot SKU 가 ${skus.length}개입니다. VITE_M365_GRANT_SKU 로 부여할 SKU 를 정해야 합니다.`,
          }
        }
        const user = await call(service, 'GetUser', email, 'id,userPrincipalName,usageLocation')
        if (!text(user, 'usageLocation')) return { ok: false, message: '사용 위치(usageLocation)가 없어 부여할 수 없습니다.' }
        await call(service, 'AssignLicense', text(user, 'id'), {
          addLicenses: [{ skuId: sku.id, disabledPlans: [] }],
          removeLicenses: [],
        })
        return { ok: true, message: `${sku.name} 라이선스를 부여했습니다.` }
      } catch (error) {
        return { ok: false, message: errorText(error) }
      }
    },
    async revoke(member): Promise<ActionResult> {
      const [userId, skuId] = member.id.split('|')
      try {
        await call(service, 'AssignLicense', userId, { addLicenses: [], removeLicenses: [skuId] })
        return { ok: true, message: '직접 할당한 라이선스를 회수했습니다.' }
      } catch (error) {
        return { ok: false, message: errorText(error) }
      }
    },
  }
}

import { describe, expect, it } from 'vitest'
import { countActive, isCandidate, judge } from './inactivity'
import { mergePeople, splitEmails } from './people'
import type { Member } from './types'

const AS_OF = '2026-11-24'

function member(over: Partial<Member>): Member {
  return { id: 'x', email: 'a@contoso.com', name: 'A', role: '사용자', status: 'active', revocable: true, ...over }
}

describe('judge', () => {
  it('기준일 이상 쓰지 않았으면 미사용', () => {
    expect(judge(member({ lastActivity: '2026-10-25' }), 30, AS_OF)).toBe('미사용')
    expect(judge(member({ lastActivity: '2026-10-26' }), 30, AS_OF)).toBe('사용 중')
  })
  it('값이 없으면 추측하지 않고 판정 불가', () => {
    expect(judge(member({}), 30, AS_OF)).toBe('판정 불가')
    expect(isCandidate('판정 불가')).toBe(false)
  })
  it('사용 이력 없음은 후보지만 미사용과 구분한다', () => {
    const verdict = judge(member({ lastActivity: null }), 30, AS_OF)
    expect(verdict).toBe('사용 이력 없음')
    expect(isCandidate(verdict)).toBe(true)
  })
  it('받은 지 14일이 안 됐으면 신규라서 후보가 아니다', () => {
    const verdict = judge(member({ addedAt: '2026-11-15', lastActivity: null }), 30, AS_OF)
    expect(verdict).toBe('신규')
    expect(isCandidate(verdict)).toBe(false)
  })
  it('초대 대기는 후보가 아니다', () => {
    expect(judge(member({ status: 'pending', lastActivity: null }), 30, AS_OF)).toBe('초대 대기')
  })
})

describe('countActive', () => {
  it('기간 안에 쓴 사람만 센다', () => {
    const members = [
      member({ lastActivity: '2026-11-20' }),
      member({ lastActivity: '2026-09-01' }),
      member({ lastActivity: null }),
      member({}),
      member({ status: 'pending', lastActivity: '2026-11-20' }),
    ]
    expect(countActive(members, 30, AS_OF)).toBe(1)
  })
})

describe('mergePeople', () => {
  it('대소문자가 달라도 같은 이메일이면 한 사람으로 묶는다', () => {
    const people = mergePeople({
      m365: { members: [member({ email: 'Kim@Contoso.com', name: '김' })], seats: { total: 1, used: 1, pending: 0 }, warnings: [] },
      claude: { members: [member({ email: 'kim@contoso.com', name: '' })], seats: { total: 1, used: 1, pending: 0 }, warnings: [] },
    })
    expect(people).toHaveLength(1)
    expect(people[0].name).toBe('김')
    expect(Object.keys(people[0].access).sort()).toEqual(['claude', 'm365'])
  })
})

describe('splitEmails', () => {
  it('구분자를 섞어 써도 나누고, 중복과 잘못된 형식을 걸러낸다', () => {
    const result = splitEmails('a@contoso.com, B@contoso.com\nb@contoso.com; 홍길동  ')
    expect(result.emails).toEqual(['a@contoso.com', 'b@contoso.com'])
    expect(result.ignored).toEqual(['홍길동'])
  })
  it('빈 입력은 빈 결과', () => {
    expect(splitEmails('  ')).toEqual({ emails: [], ignored: [] })
  })
})

import type { Member, ServiceId } from '../../domain/types'

/**
 * 예시 데이터. 실제 조직 정보가 아니며 모두 가상의 인물(contoso.com)이다.
 * 날짜는 '오늘에서 며칠 전'으로 적어 두고 화면을 열 때 실제 날짜로 바꾼다.
 */

interface Seed {
  key: string
  name: string
  role?: string
  /** 권한을 받은 지 며칠 됐는지 */
  added: number
  /** 마지막 사용이 며칠 전인지. null = 사용 이력 없음, undefined = 데이터 없음 */
  last?: number | null
  pending?: boolean
  detail?: string
  blocked?: string
}

const PEOPLE: Record<string, string> = {
  haneul: '김하늘',
  doyun: '이도윤',
  seoyeon: '박서연',
  jiho: '최지호',
  minseo: '정민서',
  yujin: '강유진',
  junwoo: '윤준우',
  sua: '임수아',
  hyunwoo: '한현우',
  jiwon: '오지원',
  taeyang: '서태양',
  nari: '문나리',
}

const SEEDS: Record<ServiceId, Seed[]> = {
  m365: [
    { key: 'haneul', name: '', added: 210, last: 1, detail: '직접 할당' },
    { key: 'doyun', name: '', added: 210, last: 3, detail: '그룹 상속 · Copilot-Users', blocked: '그룹에서 상속받은 라이선스입니다. 그룹에서 제외해야 회수됩니다.' },
    { key: 'seoyeon', name: '', added: 180, last: 52, detail: '직접 할당' },
    { key: 'jiho', name: '', added: 180, last: null, detail: '직접 할당' },
    { key: 'minseo', name: '', added: 120, last: 12, detail: '그룹 상속 · Copilot-Users', blocked: '그룹에서 상속받은 라이선스입니다. 그룹에서 제외해야 회수됩니다.' },
    { key: 'yujin', name: '', added: 6, last: null, detail: '직접 할당' },
    { key: 'junwoo', name: '', added: 95, last: 71, detail: '직접 + 그룹', blocked: '직접 할당을 회수해도 그룹 상속이 남습니다. 그룹에서도 제외해야 합니다.' },
    { key: 'sua', name: '', added: 300, last: 0, detail: '직접 할당' },
  ],
  claude: [
    { key: 'haneul', name: '', role: '소유자', added: 150, last: 0, blocked: '소유자 역할은 API 로 제거할 수 없습니다.' },
    { key: 'doyun', name: '', role: '사용자', added: 140, last: 2 },
    { key: 'hyunwoo', name: '', role: '사용자', added: 140, last: 44 },
    { key: 'jiwon', name: '', role: '사용자', added: 90, last: 5 },
    { key: 'taeyang', name: '', role: '사용자', added: 60, last: null },
    { key: 'nari', name: '', role: '사용자', added: 2, pending: true },
  ],
  cursor: [
    { key: 'doyun', name: '', role: 'member', added: 200, last: 0, detail: '이번 주기 $41.20' },
    { key: 'hyunwoo', name: '', role: 'member', added: 200, last: 1, detail: '이번 주기 $18.75' },
    { key: 'jiwon', name: '', role: 'owner', added: 260, last: 9, detail: '이번 주기 $6.10', blocked: '소유자는 이 앱에서 제거하지 않습니다.' },
    { key: 'junwoo', name: '', role: 'member', added: 75, last: 63, detail: '이번 주기 $0.00' },
    { key: 'taeyang', name: '', role: 'member', added: 40, last: 33, detail: '이번 주기 $0.00' },
  ],
  chatgpt: [
    { key: 'haneul', name: '', role: 'owner', added: 400, last: 0 },
    { key: 'seoyeon', name: '', role: 'member', added: 320, last: 4 },
    { key: 'minseo', name: '', role: 'member', added: 320, last: 1 },
    { key: 'yujin', name: '', role: 'member', added: 250 },
    { key: 'sua', name: '', role: 'member', added: 250, last: 88 },
    { key: 'jiwon', name: '', role: 'member', added: 100, last: 38 },
    { key: 'nari', name: '', role: 'member', added: 20, last: 2 },
  ],
}

export const DEMO_TOTAL_SEATS: Record<ServiceId, number | null> = {
  m365: 10,
  claude: 8,
  cursor: 5,
  chatgpt: 10,
}

function daysAgo(days: number, now: Date): string {
  return new Date(now.getTime() - days * 86_400_000).toISOString().slice(0, 10)
}

export function buildDemoMembers(service: ServiceId, now: Date = new Date()): Member[] {
  return SEEDS[service].map((seed) => ({
    id: `${service}-${seed.key}`,
    email: `${seed.key}@contoso.com`,
    name: PEOPLE[seed.key],
    role: seed.role ?? '사용자',
    status: seed.pending ? 'pending' : 'active',
    addedAt: daysAgo(seed.added, now),
    lastActivity: seed.last === undefined || seed.last === null ? seed.last : daysAgo(seed.last, now),
    detail: seed.detail,
    revocable: !seed.blocked,
    revokeBlockedReason: seed.blocked,
  }))
}

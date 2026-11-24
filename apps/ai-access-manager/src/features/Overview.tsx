import type { ServiceAdapter, ServiceId } from '../domain/types'
import { SERVICE_LABEL, SERVICE_ORDER } from '../domain/types'
import { countActive, isCandidate, judge, todayIso } from '../domain/inactivity'
import type { Person } from '../domain/people'
import { ModeBadge, SeatBar, ServiceDot, type LoadState } from './common'

interface Props {
  adapters: Record<ServiceId, ServiceAdapter>
  states: Record<ServiceId, LoadState>
  people: Person[]
  inactiveDays: number
  onOpen: (service: ServiceId) => void
}

export function Overview({ adapters, states, people, inactiveDays, onOpen }: Props) {
  const multi = people.filter((p) => Object.keys(p.access).length >= 2).length
  let candidates = 0
  for (const id of SERVICE_ORDER) {
    const state = states[id]
    if (state.status !== 'ready') continue
    const asOf = state.snapshot.asOf ?? todayIso()
    candidates += state.snapshot.members.filter((m) => isCandidate(judge(m, inactiveDays, asOf))).length
  }

  return (
    <section>
      <div className="stat-row">
        <div className="stat">
          <div className="stat-value">{people.length}</div>
          <div className="stat-label">권한이 있는 사람</div>
        </div>
        <div className="stat">
          <div className="stat-value">{multi}</div>
          <div className="stat-label">2개 이상 서비스 보유</div>
        </div>
        <div className="stat">
          <div className="stat-value">{candidates}</div>
          <div className="stat-label">정리 후보 ({inactiveDays}일 이상 미사용·이력 없음)</div>
        </div>
      </div>

      <div className="card-grid">
        {SERVICE_ORDER.map((id) => {
          const state = states[id]
          return (
            <button type="button" key={id} className="card" onClick={() => onOpen(id)}>
              <div className="card-head">
                <span className="card-title">
                  <ServiceDot service={id} />
                  {SERVICE_LABEL[id]}
                </span>
                <ModeBadge adapter={adapters[id]} />
              </div>
              {state.status === 'loading' && <p className="muted">불러오는 중…</p>}
              {state.status === 'error' && <p className="error-text">불러오지 못했습니다: {state.message}</p>}
              {state.status === 'ready' && <CardBody state={state} inactiveDays={inactiveDays} />}
            </button>
          )
        })}
      </div>
    </section>
  )
}

function CardBody({ state, inactiveDays }: { state: Extract<LoadState, { status: 'ready' }>; inactiveDays: number }) {
  const { snapshot } = state
  const asOf = snapshot.asOf ?? todayIso()
  const verdicts = snapshot.members.map((m) => judge(m, inactiveDays, asOf))
  const unknown = verdicts.filter((v) => v === '판정 불가').length
  return (
    <>
      <SeatBar seats={snapshot.seats} />
      <dl className="facts">
        <div>
          <dt>최근 30일 사용</dt>
          <dd>{unknown === snapshot.members.length ? '—' : `${countActive(snapshot.members, 30, asOf)}명`}</dd>
        </div>
        <div>
          <dt>미사용</dt>
          <dd>{verdicts.filter((v) => v === '미사용').length}명</dd>
        </div>
        <div>
          <dt>이력 없음</dt>
          <dd>{verdicts.filter((v) => v === '사용 이력 없음').length}명</dd>
        </div>
        <div>
          <dt>판정 불가</dt>
          <dd>{unknown}명</dd>
        </div>
      </dl>
    </>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import { createAdapters } from './connectors'
import { mergePeople } from './domain/people'
import type { LogEntry, ServiceId, ServiceSnapshot } from './domain/types'
import { SERVICE_LABEL, SERVICE_ORDER } from './domain/types'
import { ServiceDot, type ChangeRequest, type LoadState } from './features/common'
import { ConfirmDialog } from './features/ConfirmDialog'
import { LogPage } from './features/LogPage'
import { Overview } from './features/Overview'
import { PeoplePage } from './features/PeoplePage'
import { ServicePage } from './features/ServicePage'
import { currentUserName } from './lib/user'

type Tab = 'overview' | 'people' | ServiceId | 'log'

const LOADING: Record<ServiceId, LoadState> = {
  m365: { status: 'loading' },
  claude: { status: 'loading' },
  cursor: { status: 'loading' },
  chatgpt: { status: 'loading' },
}

function App() {
  const adapters = useMemo(() => createAdapters(), [])
  const [states, setStates] = useState<Record<ServiceId, LoadState>>(LOADING)
  const [tab, setTab] = useState<Tab>('overview')
  const [simulate, setSimulate] = useState(true)
  const [inactiveDays, setInactiveDays] = useState(30)
  const [userName, setUserName] = useState('')
  const [pending, setPending] = useState<ChangeRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [log, setLog] = useState<LogEntry[]>([])

  // 서비스 하나를 읽어 화면 상태 값으로 바꾼다. 실패해도 예외 대신 오류 상태를 돌려준다.
  const fetchState = useCallback(
    async (id: ServiceId): Promise<LoadState> => {
      try {
        return { status: 'ready', snapshot: await adapters[id].load() }
      } catch (error) {
        return { status: 'error', message: error instanceof Error ? error.message : String(error) }
      }
    },
    [adapters],
  )

  const reload = useCallback(
    (id: ServiceId) => {
      setStates((current) => ({ ...current, [id]: { status: 'loading' } }))
      void fetchState(id).then((state) => setStates((current) => ({ ...current, [id]: state })))
    },
    [fetchState],
  )

  useEffect(() => {
    for (const id of SERVICE_ORDER) {
      void fetchState(id).then((state) => setStates((current) => ({ ...current, [id]: state })))
    }
    void currentUserName().then(setUserName)
  }, [fetchState])

  const people = useMemo(() => {
    const snapshots: Partial<Record<ServiceId, ServiceSnapshot>> = {}
    for (const id of SERVICE_ORDER) {
      const state = states[id]
      if (state.status === 'ready') snapshots[id] = state.snapshot
    }
    return mergePeople(snapshots)
  }, [states])

  async function execute(request: ChangeRequest) {
    setBusy(true)
    const adapter = adapters[request.service]
    const entries: LogEntry[] = []
    const record = (target: string, outcome: LogEntry['outcome'], message: string) =>
      entries.push({
        at: new Date().toISOString(),
        actor: userName || '알 수 없음',
        service: request.service,
        action: request.action,
        target,
        outcome,
        message,
      })

    try {
      if (request.action === '부여') {
        for (const email of request.emails ?? []) {
          if (simulate) record(email, '시뮬레이션', '실제로는 변경하지 않았습니다.')
          else {
            const result = await adapter.grant(email)
            record(email, result.ok ? '성공' : '실패', result.message)
          }
        }
      } else {
        for (const member of request.members ?? []) {
          if (simulate) record(member.email, '시뮬레이션', '실제로는 변경하지 않았습니다.')
          else {
            const result = await adapter.revoke(member)
            record(member.email, result.ok ? '성공' : '실패', result.message)
          }
        }
      }
    } finally {
      const count = (outcome: LogEntry['outcome']) => entries.filter((e) => e.outcome === outcome).length
      setLog((current) => [...entries.reverse(), ...current])
      setNotice(
        `${SERVICE_LABEL[request.service]} ${request.action} 결과 — 성공 ${count('성공')} · 실패 ${count('실패')} · 시뮬레이션 ${count('시뮬레이션')}`,
      )
      setPending(null)
      setBusy(false)
      if (!simulate) reload(request.service)
    }
  }

  const demoCount = SERVICE_ORDER.filter((id) => adapters[id].mode === 'demo').length
  const tabs: { id: Tab; label: string; service?: ServiceId }[] = [
    { id: 'overview', label: '전체 현황' },
    { id: 'people', label: '사용자별' },
    ...SERVICE_ORDER.map((id) => ({ id: id as Tab, label: SERVICE_LABEL[id], service: id })),
    { id: 'log', label: `작업 기록${log.length ? ` ${log.length}` : ''}` },
  ]

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>AI 라이선스 관리</h1>
          <p className="muted small">Microsoft 365 Copilot · Claude · Cursor · ChatGPT 권한을 한 화면에서 조회하고 정리합니다</p>
        </div>
        <div className="top-controls">
          <label className="field">
            미사용 기준
            <select value={inactiveDays} onChange={(event) => setInactiveDays(Number(event.target.value))}>
              {[30, 60, 90, 180].map((days) => (
                <option key={days} value={days}>
                  {days}일
                </option>
              ))}
            </select>
          </label>
          <label className={`switch${simulate ? '' : ' live'}`}>
            <input type="checkbox" checked={simulate} onChange={(event) => setSimulate(event.target.checked)} />
            {simulate ? '시뮬레이션 켜짐' : '실제 적용'}
          </label>
          <span className="user">{userName}</span>
        </div>
      </header>

      {!simulate && (
        <p className="callout bad banner">
          실제 적용 모드입니다. 부여·회수가 각 서비스에 바로 반영됩니다.
        </p>
      )}
      {demoCount > 0 && (
        <p className="callout info banner">
          {demoCount === SERVICE_ORDER.length ? '모든' : `${demoCount}개`} 서비스가 아직 연결되지 않아 예시 데이터(가상 인물)를
          보여 줍니다. 커넥터를 연결하면 해당 서비스부터 실제 데이터로 바뀝니다.
        </p>
      )}
      {notice && (
        <p className="callout ok banner" role="status">
          {notice}
          <button type="button" className="link" onClick={() => setNotice('')}>
            닫기
          </button>
        </p>
      )}

      <nav className="tabs" aria-label="화면 선택">
        {tabs.map((item) => (
          <button
            type="button"
            key={item.id}
            className={tab === item.id ? 'on' : ''}
            aria-current={tab === item.id ? 'page' : undefined}
            onClick={() => setTab(item.id)}
          >
            {item.service && <ServiceDot service={item.service} />}
            {item.label}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'overview' && (
          <Overview adapters={adapters} states={states} people={people} inactiveDays={inactiveDays} onOpen={setTab} />
        )}
        {tab === 'people' && (
          <PeoplePage adapters={adapters} states={states} people={people} inactiveDays={inactiveDays} onChange={setPending} />
        )}
        {SERVICE_ORDER.map(
          (id) =>
            tab === id && (
              <ServicePage
                key={id}
                adapter={adapters[id]}
                state={states[id]}
                inactiveDays={inactiveDays}
                onChange={setPending}
                onReload={() => reload(id)}
              />
            ),
        )}
        {tab === 'log' && <LogPage entries={log} />}
      </main>

      {pending && (
        <ConfirmDialog
          request={pending}
          simulate={simulate}
          busy={busy}
          onConfirm={() => void execute(pending)}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )
}

export default App

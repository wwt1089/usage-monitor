/**
 * `ms app add data-source` 가 만들어 주는 generated/ 폴더의 커넥터 코드를 찾는다.
 * 폴더가 아직 없어도 빌드가 되도록 정적 import 대신 glob 을 쓴다 (없으면 빈 객체).
 * generated/ 를 읽는 곳은 src/connectors/ 뿐이다.
 */

export interface OperationResult {
  success: boolean
  data?: unknown
  error?: unknown
}

type Operation = (...args: unknown[]) => Promise<OperationResult>
export type GeneratedService = Record<string, Operation>

const modules = import.meta.glob('../../generated/index.ts', { eager: true }) as Record<
  string,
  Record<string, unknown>
>

/**
 * 이름이 pattern 에 맞고 필요한 동작을 모두 가진 서비스를 돌려준다. 없으면 null.
 * 서비스 이름은 커넥터 표시 이름에서 만들어지므로(예: "Claude Admin" → ClaudeAdminService) 느슨하게 찾는다.
 */
export function findService(pattern: RegExp, operations: string[]): GeneratedService | null {
  for (const exportsOfModule of Object.values(modules)) {
    for (const [name, value] of Object.entries(exportsOfModule)) {
      if (!pattern.test(name) || (typeof value !== 'function' && typeof value !== 'object') || value === null) continue
      const service = value as GeneratedService
      if (operations.every((op) => typeof service[op] === 'function')) return service
    }
  }
  return null
}

export function errorText(error: unknown, fallback = '알 수 없는 오류'): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string' && error.trim()) return error
  if (typeof error === 'object' && error !== null) {
    const record = error as Record<string, unknown>
    const nested = record.error
    if (typeof nested === 'object' && nested !== null) {
      const message = (nested as Record<string, unknown>).message
      if (typeof message === 'string' && message.trim()) return message
    }
    if (typeof record.message === 'string' && record.message.trim()) return record.message
    try {
      return JSON.stringify(error)
    } catch {
      return fallback
    }
  }
  return fallback
}

/** 커넥터 동작을 부르고 실패하면 예외로 바꾼다. */
export async function call(service: GeneratedService, operation: string, ...args: unknown[]): Promise<unknown> {
  const run = service[operation]
  if (typeof run !== 'function') throw new Error(`커넥터에 ${operation} 동작이 없습니다.`)
  const result = await run(...args)
  if (!result || result.success === false) {
    throw new Error(errorText(result?.error, `${operation} 호출이 실패했습니다.`))
  }
  return result.data
}

// --- 응답을 느슨하게 읽는 도우미: 문서와 실제 응답의 필드 이름이 조금 달라도 버틴다 ---

export function rec(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

export function list(value: unknown, ...keys: string[]): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.map(rec)
  const source = rec(value)
  for (const key of keys) {
    if (Array.isArray(source[key])) return (source[key] as unknown[]).map(rec)
  }
  return []
}

export function text(value: unknown, ...keys: string[]): string {
  const source = rec(value)
  for (const key of keys) {
    const found = source[key]
    if (typeof found === 'string' && found.trim()) return found
    if (typeof found === 'number') return String(found)
  }
  return ''
}

/** 날짜 값(ISO 문자열 또는 epoch 밀리초)을 YYYY-MM-DD 로 바꾼다. 못 읽으면 ''. */
export function isoDay(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return new Date(value).toISOString().slice(0, 10)
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) return value.slice(0, 10)
  if (typeof value === 'string' && /^\d{12,}$/.test(value)) return new Date(Number(value)).toISOString().slice(0, 10)
  return ''
}

export function dayOffset(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + days * 86_400_000).toISOString().slice(0, 10)
}

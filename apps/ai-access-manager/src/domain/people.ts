import type { Member, ServiceId, ServiceSnapshot } from './types'
import { SERVICE_ORDER } from './types'

export interface Person {
  email: string
  name: string
  access: Partial<Record<ServiceId, Member>>
}

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase()
}

/** 서비스별 목록을 이메일 기준으로 한 사람씩 묶는다. */
export function mergePeople(snapshots: Partial<Record<ServiceId, ServiceSnapshot>>): Person[] {
  const byEmail = new Map<string, Person>()
  for (const service of SERVICE_ORDER) {
    for (const member of snapshots[service]?.members ?? []) {
      const key = normalizeEmail(member.email)
      if (!key) continue
      const person = byEmail.get(key) ?? { email: key, name: member.name, access: {} }
      if (!person.name && member.name) person.name = member.name
      person.access[service] = member
      byEmail.set(key, person)
    }
  }
  return [...byEmail.values()].sort((a, b) => a.email.localeCompare(b.email))
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** 입력 칸의 여러 이메일을 나눈다. 형식이 틀린 값은 따로 돌려준다. */
export function splitEmails(input: string): { emails: string[]; ignored: string[] } {
  const emails: string[] = []
  const ignored: string[] = []
  for (const raw of input.split(/[\s,;]+/)) {
    const value = normalizeEmail(raw)
    if (!value) continue
    if (!EMAIL.test(value)) ignored.push(raw.trim())
    else if (!emails.includes(value)) emails.push(value)
  }
  return { emails, ignored }
}

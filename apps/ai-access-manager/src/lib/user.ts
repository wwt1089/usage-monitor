import { getUser } from '@microsoft/cmr-apps/auth'

/**
 * 로그인한 사용자 이름. Managed Runtime 안에서는 호스트가 알려 주고,
 * 로컬에서 그냥 열었을 때는 호스트가 없어 응답이 없으므로 잠시 기다린 뒤 대체 이름을 쓴다.
 */
export async function currentUserName(timeoutMs = 2500): Promise<string> {
  const fallback = '로컬 미리보기'
  try {
    const user = await Promise.race([
      getUser(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ])
    return user?.fullName?.trim() || fallback
  } catch {
    return fallback
  }
}

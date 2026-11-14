// Public demo credentials show channel/scope gates, not production authentication.
export const DEMO_ADMIN_STORAGE = 'verified-or-ask.demo-admin-key'
const ROLE_STORAGE = 'verified-or-ask.demo-approver-role'

export function agentHeaders(customerId: string): Record<string, string> {
  return { Authorization: `Bearer demo-agent-${customerId}` }
}

export function adminHeaders(): Record<string, string> {
  const key = sessionStorage.getItem(DEMO_ADMIN_STORAGE)
  return key ? { 'X-Demo-Admin-Key': key } : {}
}

export function chooseApproverRole(role: string) {
  sessionStorage.setItem(ROLE_STORAGE, role)
}

export function chosenApproverRole(): string | null {
  return sessionStorage.getItem(ROLE_STORAGE)
}

export function approverHeaders(): Record<string, string> {
  const role = sessionStorage.getItem(ROLE_STORAGE)
  return role ? { 'X-Demo-Approver-Role': role } : {}
}

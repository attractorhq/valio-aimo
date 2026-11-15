import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, KeyRound, LoaderCircle } from 'lucide-react'
import { chosenApproverRole } from '../lib/demoAccess'

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (value.length % 4)) % 4)
  const raw = atob(padded)
  const buffer = new ArrayBuffer(raw.length)
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < raw.length; i += 1) {
    bytes[i] = raw.charCodeAt(i)
  }
  return bytes
}

function toBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function authHeaders(role?: string | null, approvalToken?: string): Record<string, string> {
  const headers: Record<string, string> = {}
  const activeRole = role ?? chosenApproverRole() ?? undefined
  if (activeRole) headers['X-Demo-Approver-Role'] = activeRole
  if (approvalToken) headers['X-Approval-Token'] = approvalToken
  return headers
}

type PasskeyStatus = {
  role: string
  enrolled: boolean
  credentialCount: number
}

let fetchInterceptorInstalled = false

export function installPasskeyDecisionInterceptor(): void {
  if (fetchInterceptorInstalled || typeof window === 'undefined' || typeof window.fetch !== 'function') {
    return
  }
  fetchInterceptorInstalled = true
  const originalFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const match = url.match(/\/v0\.1\/approver\/proposals\/([^/]+)\/decision(?:\?|$)/)
    if (
      match &&
      init?.method?.toUpperCase() === 'POST' &&
      typeof init.body === 'string' &&
      typeof navigator !== 'undefined' &&
      navigator.credentials
    ) {
      try {
        const parsed = JSON.parse(init.body) as Record<string, unknown>
        if (parsed.action === 'APPROVE' && !parsed.passkeyAssertion) {
          const proposalId = decodeURIComponent(match[1])
          const approvalToken = typeof parsed.approvalToken === 'string' ? parsed.approvalToken : undefined
          const rpId = window.location.hostname || 'localhost'
          const optRes = await originalFetch(
            `/v0.1/approver/proposals/${encodeURIComponent(proposalId)}/passkey-options?rpId=${encodeURIComponent(rpId)}`,
            {
              headers: {
                Accept: 'application/json',
                ...authHeaders(undefined, approvalToken),
              },
            },
          )
          if (optRes.ok) {
            const optData = (await optRes.json()) as {
              enrolled?: boolean
              options?: {
                challenge: string
                rpId?: string
                timeout?: number
                userVerification?: UserVerificationRequirement
                allowCredentials?: Array<{ id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] }>
              }
            }
            if (optData.enrolled && optData.options) {
              const publicKey: PublicKeyCredentialRequestOptions = {
                challenge: fromBase64Url(optData.options.challenge),
                rpId: optData.options.rpId ?? rpId,
                timeout: optData.options.timeout ?? 60000,
                userVerification: optData.options.userVerification ?? 'preferred',
                allowCredentials: (optData.options.allowCredentials ?? []).map((c) => ({
                  id: fromBase64Url(c.id),
                  type: c.type,
                  transports: c.transports,
                })),
              }
              const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null
              if (cred) {
                const resp = cred.response as AuthenticatorAssertionResponse
                parsed.passkeyAssertion = {
                  id: cred.id,
                  rawId: toBase64Url(cred.rawId),
                  type: cred.type,
                  authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
                  response: {
                    authenticatorData: toBase64Url(resp.authenticatorData),
                    clientDataJSON: toBase64Url(resp.clientDataJSON),
                    signature: toBase64Url(resp.signature),
                    userHandle: resp.userHandle ? toBase64Url(resp.userHandle) : null,
                  },
                }
                init = { ...init, body: JSON.stringify(parsed) }
              }
            }
          }
        }
      } catch {
        // If WebAuthn is cancelled or unavailable, let the server return its authoritative error.
      }
    }
    return originalFetch(input, init)
  }
}

export function ApproverPasskeyControl({
  role,
  approvalToken,
}: {
  role?: string
  approvalToken?: string
}) {
  useEffect(() => {
    installPasskeyDecisionInterceptor()
  }, [])

  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const activeRole = role ?? chosenApproverRole() ?? undefined

  const statusQuery = useQuery({
    queryKey: ['approver-passkey-status', activeRole, approvalToken],
    queryFn: async (): Promise<PasskeyStatus> => {
      const params = activeRole ? `?role=${encodeURIComponent(activeRole)}` : ''
      const res = await fetch(`/v0.1/approver/passkey/status${params}`, {
        headers: { Accept: 'application/json', ...authHeaders(activeRole, approvalToken) },
      })
      if (!res.ok) {
        return { role: activeRole ?? 'Head Chef', enrolled: false, credentialCount: 0 }
      }
      return (await res.json()) as PasskeyStatus
    },
    enabled: Boolean(activeRole || approvalToken),
  })

  const enrol = useMutation({
    mutationFn: async () => {
      if (typeof navigator === 'undefined' || !navigator.credentials) {
        throw new Error('WebAuthn passkeys are not supported in this browser.')
      }
      const rpId = window.location.hostname || 'localhost'
      const optRes = await fetch('/v0.1/approver/passkey/register/options', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...authHeaders(activeRole, approvalToken),
        },
        body: JSON.stringify({ role: activeRole, rpId, approvalToken }),
      })
      if (!optRes.ok) {
        throw new Error('Could not start passkey enrolment.')
      }
      const { options } = (await optRes.json()) as {
        options: {
          rp: PublicKeyCredentialRpEntity
          user: { id: string; name: string; displayName: string }
          challenge: string
          pubKeyCredParams: PublicKeyCredentialParameters[]
          timeout?: number
          excludeCredentials?: Array<{ id: string; type: PublicKeyCredentialType }>
          authenticatorSelection?: AuthenticatorSelectionCriteria
          attestation?: AttestationConveyancePreference
        }
      }
      const publicKey: PublicKeyCredentialCreationOptions = {
        rp: options.rp,
        user: {
          id: fromBase64Url(options.user.id),
          name: options.user.name,
          displayName: options.user.displayName,
        },
        challenge: fromBase64Url(options.challenge),
        pubKeyCredParams: options.pubKeyCredParams,
        timeout: options.timeout ?? 60000,
        excludeCredentials: (options.excludeCredentials ?? []).map((c) => ({
          id: fromBase64Url(c.id),
          type: c.type,
        })),
        authenticatorSelection: options.authenticatorSelection,
        attestation: options.attestation ?? 'none',
      }
      const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null
      if (!cred) {
        throw new Error('Passkey creation was cancelled.')
      }
      const resp = cred.response as AuthenticatorAttestationResponse
      const verifyRes = await fetch('/v0.1/approver/passkey/register/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...authHeaders(activeRole, approvalToken),
        },
        body: JSON.stringify({
          role: activeRole,
          rpId,
          approvalToken,
          credential: {
            id: cred.id,
            rawId: toBase64Url(cred.rawId),
            type: cred.type,
            authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
            response: {
              clientDataJSON: toBase64Url(resp.clientDataJSON),
              attestationObject: toBase64Url(resp.attestationObject),
              transports: typeof resp.getTransports === 'function' ? resp.getTransports() : undefined,
            },
          },
        }),
      })
      if (!verifyRes.ok) {
        throw new Error('Passkey verification failed on the server.')
      }
      return (await verifyRes.json()) as PasskeyStatus
    },
    onSuccess: (result) => {
      setNotice(`Passkey enrolled for ${result.role}`)
      void queryClient.invalidateQueries({ queryKey: ['approver-passkey-status'] })
    },
    onError: (err) => {
      setNotice(err instanceof Error ? err.message : 'Passkey enrolment failed.')
    },
  })

  if (!activeRole && !approvalToken) return null

  const enrolled = statusQuery.data?.enrolled ?? false
  const resolvedRole = statusQuery.data?.role ?? activeRole ?? 'Approver'

  return (
    <div className="inline-flex flex-wrap items-center gap-2 text-xs" data-testid="passkey-control">
      {enrolled ? (
        <span className="chip inline-flex items-center gap-1 font-semibold">
          <CheckCircle2 size={13} aria-hidden />
          <span>Passkey active · human:{resolvedRole} (passkey)</span>
        </span>
      ) : null}
      <button
        type="button"
        className="btn btn-scenario inline-flex items-center gap-1.5 text-xs font-semibold"
        onClick={() => enrol.mutate()}
        disabled={enrol.isPending}
      >
        {enrol.isPending ? <LoaderCircle size={13} aria-hidden className="spin" /> : <KeyRound size={13} aria-hidden />}
        <span>Enrol this phone</span>
      </button>
      {notice && <span className="muted text-xs">{notice}</span>}
    </div>
  )
}

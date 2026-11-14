import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { approverHeaders } from '../lib/demoAccess'
import { approverChannel, type DecideInput } from '../lib/gateway'

function supportsPasskeys() {
  return (
    window.isSecureContext &&
    typeof PublicKeyCredential !== 'undefined' &&
    typeof PublicKeyCredential.parseCreationOptionsFromJSON === 'function' &&
    typeof PublicKeyCredential.parseRequestOptionsFromJSON === 'function' &&
    typeof PublicKeyCredential.prototype.toJSON === 'function'
  )
}

async function passkeyRequest<T>(path: string, body?: object): Promise<T | null> {
  const response = await fetch(`/v0.1/approver/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...approverHeaders(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(12000),
  })
  if (response.status === 404) return null
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new Error(data?.error?.message ?? 'The passkey could not be verified.')
  }
  return response.json()
}

export async function decideWithPasskey(input: DecideInput) {
  if (
    input.action === 'REJECT' ||
    input.proposalId.startsWith('MOCK-') ||
    input.approvalToken.startsWith('tk-')
  ) {
    return approverChannel.decide(input)
  }

  const options = await passkeyRequest<PublicKeyCredentialRequestOptionsJSON>(
    `proposals/${encodeURIComponent(input.proposalId)}/passkey/options`,
    { approvalToken: input.approvalToken, offerVersion: input.offerVersion },
  )
  if (!options) return approverChannel.decide(input)
  if (!supportsPasskeys()) {
    throw new Error('This approver requires a passkey. Open the link in a passkey-enabled browser.')
  }
  const credential = await navigator.credentials
    .get({
      publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(options),
    })
    .catch((error) => {
      if (error instanceof DOMException && error.name === 'NotAllowedError') {
        throw new Error(
          'Passkey verification was cancelled or unavailable. Try again on the enrolled device.',
        )
      }
      throw error
    })
  if (!(credential instanceof PublicKeyCredential))
    throw new Error('Passkey verification was cancelled.')
  return approverChannel.decide({ ...input, passkeyAssertion: credential.toJSON() })
}

export function PasskeyEnrolment({ customerId, role }: { customerId: string; role: string }) {
  const queryClient = useQueryClient()
  const path = `passkeys/${encodeURIComponent(customerId)}`
  const queryKey = ['approver-passkey', customerId, role]
  const status = useQuery({
    queryKey,
    queryFn: () => passkeyRequest<{ enrolled: boolean }>(path),
    enabled: !customerId.startsWith('mock-'),
    retry: false,
  })
  const enrol = useMutation({
    mutationFn: async () => {
      const options = await passkeyRequest<PublicKeyCredentialCreationOptionsJSON>(
        `${path}/options`,
        {},
      )
      if (!options) throw new Error('Passkey enrolment is unavailable on this gateway.')
      const credential = await navigator.credentials
        .create({
          publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(options),
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === 'NotAllowedError') {
            throw new Error('Passkey enrolment was cancelled or the device could not verify you.')
          }
          throw error
        })
      if (!(credential instanceof PublicKeyCredential))
        throw new Error('Passkey enrolment was cancelled.')
      const confirmed = await passkeyRequest<{ enrolled: boolean }>(path, {
        credential: credential.toJSON(),
      })
      if (!confirmed?.enrolled) throw new Error('The gateway did not register this passkey.')
      return confirmed
    },
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })

  if (!status.data) return null
  return (
    <section className="panel flex flex-wrap items-center gap-2" aria-label="Approver passkey">
      {status.data.enrolled ? (
        <p role="status" className="text-sm font-semibold">
          Passkey required for {role} approvals
        </p>
      ) : (
        <>
          <button
            type="button"
            className="btn btn-scenario"
            disabled={enrol.isPending || !supportsPasskeys()}
            onClick={() => enrol.mutate()}
          >
            {enrol.isPending ? 'Enrolling…' : 'Enrol this phone'}
          </button>
          <span className="muted small">
            {supportsPasskeys()
              ? `Set up a passkey for ${role} on this device in this session.`
              : 'Open the approver panel in a passkey-enabled browser to enrol.'}
          </span>
        </>
      )}
      {enrol.error && (
        <p role="alert" className="notice notice-error">
          {enrol.error.message}
        </p>
      )}
    </section>
  )
}

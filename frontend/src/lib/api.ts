const configured = import.meta.env.VITE_API_BASE_URL as string | undefined
// Production builds are served by the edge Worker on the same origin as the gateway (T17).
const fallback = import.meta.env.PROD ? '' : 'http://localhost:8000'
export const API_BASE = (configured ?? fallback).replace(/\/$/, '')
export type Identity = 'demo-restaurant-a' | 'demo-hotel-b' | 'demo-manager'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export async function request<T>(
  identity: Identity,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${identity}`,
        ...options.headers,
      },
      signal: options.signal || AbortSignal.timeout(path.includes('/agent/') ? 150_000 : 25_000),
    })
  } catch {
    throw new Error(
      'Could not reach the gateway. Check the backend URL, server and CORS configuration.',
    )
  }
  const data = await response.json()
  if (!response.ok) {
    const fields = data.errors
      ?.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`)
      .join('; ')
    throw new ApiError(
      response.status,
      fields || data.detail || 'The gateway could not complete this action.',
    )
  }
  return data as T
}

export const money = (value: string | number) =>
  new Intl.NumberFormat('en-FI', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 2,
  }).format(Number(value))
export const readableDate = (value: string) =>
  new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeZone: 'Europe/Helsinki',
  }).format(new Date(value.length === 10 ? `${value}T12:00:00Z` : value))
export const readableTime = (value: string) =>
  new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Europe/Helsinki',
  }).format(new Date(value))

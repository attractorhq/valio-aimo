const configured = import.meta.env.VITE_API_BASE_URL as string | undefined
// Production builds are served by the edge Worker on the same origin as the gateway (T17).
const fallback = import.meta.env.PROD ? '' : 'http://localhost:8000'
export const API_BASE = (configured ?? fallback).replace(/\/$/, '')

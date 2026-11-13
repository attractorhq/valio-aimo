#!/usr/bin/env node
// Generic HTTP client for the blind judge, locked to the app's origin (JUDGE_ORIGIN).
// Usage: http <METHOD> <path-or-url> [json-body] [-H 'Name: value']...
'use strict'
const ORIGIN = new URL(process.env.JUDGE_ORIGIN || '').origin
const MAX_BODY = 20000

async function main() {
  const args = process.argv.slice(2)
  const headers = {}
  const rest = []
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '-H' && args[i + 1]) {
      const [k, ...v] = args[++i].split(':')
      headers[k.trim()] = v.join(':').trim()
    } else rest.push(args[i])
  }
  const [method = 'GET', target = '/', body] = rest
  const url = new URL(target, ORIGIN)
  if (url.origin !== ORIGIN) throw new Error(`requests outside ${ORIGIN} are not allowed`)
  if (body !== undefined && !headers['Content-Type']) headers['Content-Type'] = 'application/json'
  const started = Date.now()
  const res = await fetch(url, { method: method.toUpperCase(), headers, body, redirect: 'manual' })
  const text = await res.text()
  console.log(`HTTP ${res.status} ${res.statusText} · ${Date.now() - started} ms · ${url.href}`)
  for (const h of ['content-type', 'location', 'idempotency-key', 'etag']) {
    if (res.headers.get(h)) console.log(`${h}: ${res.headers.get(h)}`)
  }
  let out = text
  try {
    out = JSON.stringify(JSON.parse(text), null, 1)
  } catch {}
  console.log(out.slice(0, MAX_BODY) + (out.length > MAX_BODY ? '\n…(truncated)' : ''))
}

main().catch((err) => {
  console.error(`http error: ${err.message || err}`)
  process.exit(1)
})

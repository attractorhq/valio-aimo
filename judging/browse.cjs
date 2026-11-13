#!/usr/bin/env node
// Generic browser driver for the blind judge. Knows nothing about the app.
// Usage: browse '<steps-json>'   (JUDGE_ORIGIN must be set; navigation is locked to that origin)
// Steps: {"viewport":"desktop|mobile"} {"goto":"/path"} {"fill":[sel,text]} {"click":sel}
//        {"press":[sel,key]} {"wait":ms|sel} {"screenshot":name} {"text":sel}
'use strict'
const fs = require('fs')
const path = require('path')
const { chromium } = require('playwright')

const ORIGIN = new URL(process.env.JUDGE_ORIGIN || '').origin
const SHOTS = path.resolve('shots')
const MAX_TEXT = 6000

function resolveUrl(target) {
  const url = new URL(target, ORIGIN)
  if (url.origin !== ORIGIN) throw new Error(`navigation outside ${ORIGIN} is not allowed`)
  return url.href
}

async function launch() {
  try {
    return await chromium.launch({ channel: 'chrome', headless: true })
  } catch {
    return await chromium.launch({ headless: true })
  }
}

async function main() {
  const steps = JSON.parse(process.argv[2] || '[]')
  if (!Array.isArray(steps)) throw new Error('steps must be a JSON array')
  fs.mkdirSync(SHOTS, { recursive: true })
  const browser = await launch()
  let context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  let page = await context.newPage()
  // Block any request that leaves the app's origin (except data/blob URLs).
  await context.route('**/*', (route) => {
    const u = route.request().url()
    if (u.startsWith('data:') || u.startsWith('blob:') || new URL(u).origin === ORIGIN) return route.continue()
    // Third-party assets (fonts, CDNs) are allowed for rendering, but never file: URLs.
    if (u.startsWith('http://') || u.startsWith('https://')) return route.continue()
    return route.abort()
  })
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame() && new URL(frame.url()).origin !== ORIGIN && frame.url() !== 'about:blank') {
      console.log(`! left the app origin: ${frame.url()}`)
    }
  })
  for (const [i, step] of steps.entries()) {
    const [kind, arg] = Object.entries(step)[0] || []
    try {
      if (kind === 'viewport') {
        const mobile = arg === 'mobile'
        const url = page.url()
        await context.close()
        context = await browser.newContext(
          mobile
            ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }
            : { viewport: { width: 1440, height: 900 } },
        )
        page = await context.newPage()
        if (url && url !== 'about:blank') await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 })
      } else if (kind === 'goto') {
        await page.goto(resolveUrl(arg), { waitUntil: 'networkidle', timeout: 30000 })
      } else if (kind === 'fill') {
        await page.locator(arg[0]).first().fill(String(arg[1]), { timeout: 10000 })
      } else if (kind === 'click') {
        await page.locator(arg).first().click({ timeout: 10000 })
        await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
      } else if (kind === 'press') {
        await page.locator(arg[0]).first().press(arg[1], { timeout: 10000 })
        await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
      } else if (kind === 'wait') {
        if (typeof arg === 'number') await page.waitForTimeout(Math.min(arg, 30000))
        else await page.locator(arg).first().waitFor({ timeout: 30000 })
      } else if (kind === 'screenshot') {
        const file = path.join(SHOTS, `${String(arg).replace(/[^a-z0-9_-]/gi, '_')}.png`)
        await page.screenshot({ path: file, fullPage: true })
        console.log(`[${i}] screenshot -> shots/${path.basename(file)}`)
      } else if (kind === 'text') {
        const text = await page.locator(arg || 'body').first().innerText({ timeout: 10000 })
        console.log(`[${i}] text(${arg || 'body'}) @ ${page.url()}\n${text.slice(0, MAX_TEXT)}${text.length > MAX_TEXT ? '\n…(truncated)' : ''}`)
      } else {
        console.log(`[${i}] unknown step: ${JSON.stringify(step)}`)
      }
    } catch (err) {
      console.log(`[${i}] ${kind} failed: ${String(err.message || err).split('\n')[0]}`)
    }
  }
  console.log(`final url: ${page.url()}`)
  await browser.close()
}

main().catch((err) => {
  console.error(`browse error: ${err.message || err}`)
  process.exit(1)
})

"""End-to-end check of the live app's building-register lookup in a real browser.
Opens the GitHub Pages site, selects 서울특별시 강동구 명일동 94, presses 조회,
and prints the on-screen message, result cards, worker requests and console errors."""
import asyncio, sys, time
from playwright.async_api import async_playwright

URL = 'https://gurifire.github.io/fire-construction/?e2e=' + str(int(time.time()))

async def scenario(browser, label, **ctx_args):
    ctx = await browser.new_context(**ctx_args)
    page = await ctx.new_page()
    logs, reqs = [], []
    page.on('console', lambda m: logs.append(f'{m.type}: {m.text}'))
    page.on('pageerror', lambda e: logs.append(f'pageerror: {e}'))
    page.on('response', lambda r: reqs.append(f'{r.status} {r.url[:150]}') if ('workers.dev' in r.url or 'data.go.kr' in r.url or 'regions_v1' in r.url) else None)
    page.on('requestfailed', lambda r: reqs.append(f'FAILED {r.failure} {r.url[:150]}'))
    print(f'\n===== {label}')
    await page.goto(URL, wait_until='networkidle')
    print('title:', await page.title())
    await page.click('#v85-reg>summary')
    await page.wait_for_function("document.getElementById('v91-sido').options.length>1", timeout=15000)
    print('note:', await page.evaluate("document.getElementById('v91-region-note').textContent"))
    print('proxy mode:', await page.evaluate("!!document.querySelector('.v91-own-key')"))
    await page.select_option('#v91-sido', '11')
    await page.wait_for_function("[...document.getElementById('v91-sgg').options].some(o=>o.value==='11740')", timeout=15000)
    await page.select_option('#v91-sgg', '11740')
    await page.wait_for_function("[...document.getElementById('v85-dong').options].some(o=>o.value==='1174010100')", timeout=15000)
    await page.select_option('#v85-dong', '1174010100')
    await page.fill('#v85-bun', '94')
    await page.click('#v85-run')
    await page.wait_for_function("!document.getElementById('v85-run').disabled", timeout=60000)
    print('message:', await page.evaluate("document.getElementById('v85-msg').textContent"))
    print('cards:', await page.evaluate("[...document.querySelectorAll('#v85-list .v85-item')].map(e=>e.innerText.replace(/\\s+/g,' ')).slice(0,3)"))
    print('requests:'); [print('  ', r) for r in reqs]
    print('console:'); [print('  ', l) for l in logs if not l.startswith('log')]
    await ctx.close()

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await scenario(b, 'PC (Chrome 1280px)', viewport={'width': 1280, 'height': 900})
        await scenario(b, 'Mobile (Galaxy 360px)', **p.devices['Galaxy S9+'])
        await b.close()
        w = await p.webkit.launch()
        await scenario(w, 'Mobile Safari (iPhone)', **p.devices['iPhone 13'])
        await w.close()

asyncio.run(main())

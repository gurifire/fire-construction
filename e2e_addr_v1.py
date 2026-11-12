"""Live-site check: road-name/building-name search -> pick -> building register lookup."""
import asyncio, time
from playwright.async_api import async_playwright

URL = 'https://gurifire.github.io/fire-construction/?addr=' + str(int(time.time()))
KEYWORDS = ['구리역롯데캐슬', '아차산로 439', '세종대로 110', '의사당대로 1', '명일동 94', '없는건물이름가나다']

async def scenario(browser, label, **ctx_args):
    ctx = await browser.new_context(**ctx_args)
    page = await ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    print(f'\n===== {label}')
    await page.goto(URL, wait_until='networkidle')
    print('title:', await page.title())
    await page.click('#v85-reg>summary')
    await page.wait_for_function("document.getElementById('v91-sido').options.length>1", timeout=20000)
    for kw in KEYWORDS:
        await page.fill('#v99-q', kw)
        await page.click('#v99-go')
        await page.wait_for_function("!document.getElementById('v99-go').disabled", timeout=30000)
        msg = await page.evaluate("document.getElementById('v99-msg').textContent")
        cards = await page.evaluate("[...document.querySelectorAll('#v99-list .v85-item')].map(e=>e.innerText.replace(/\\s+/g,' ').replace(' 선택 ›',''))")
        print(f'[{kw}] {msg}')
        for c in cards[:3]:
            print('    -', c[:110])
        if not cards:
            continue
        await page.locator('#v99-list .v85-item').first.click()
        await page.wait_for_function("!document.getElementById('v85-run').disabled && document.getElementById('v85-msg').textContent.length>0", timeout=90000)
        sel = await page.evaluate("({sido:document.getElementById('v91-sido').selectedOptions[0]?.text,sgg:document.getElementById('v91-sgg').selectedOptions[0]?.text,dong:document.getElementById('v85-dong').selectedOptions[0]?.text,manual:document.getElementById('v85-manual').value,bun:document.getElementById('v85-bun').value,ji:document.getElementById('v85-ji').value,san:document.getElementById('v85-san').checked})")
        print(f"    선택 → {sel['sido']} {sel['sgg']} {sel['dong']}{(' ['+sel['manual']+']') if sel['manual'] else ''} {('산 ' if sel['san'] else '')}{sel['bun']}{('-'+sel['ji']) if sel['ji'] else ''}")
        print('    대장:', await page.evaluate("document.getElementById('v85-msg').textContent"))
        bl = await page.evaluate("[...document.querySelectorAll('#v85-list .v85-item')].map(e=>e.innerText.replace(/\\s+/g,' ').replace(' 채우기 ›',''))")
        for c in bl[:3]:
            print('      ·', c[:110])
        if not bl and sel['ji']:
            await page.fill('#v85-ji', '')
            await page.click('#v85-run')
            await page.wait_for_function("!document.getElementById('v85-run').disabled", timeout=90000)
            print('    부번 비우고 재조회:', await page.evaluate("document.getElementById('v85-msg').textContent"))
            bl = await page.evaluate("[...document.querySelectorAll('#v85-list .v85-item')].map(e=>e.innerText.replace(/\\s+/g,' ').replace(' 채우기 ›',''))")
            for c in bl[:3]:
                print('      ·', c[:110])
    print('errors:', errors)
    await ctx.close()

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await scenario(b, 'PC Chrome', viewport={'width': 1280, 'height': 900})
        await b.close()
        w = await p.webkit.launch()
        await scenario(w, 'iPhone Safari', **p.devices['iPhone 13'])
        await w.close()

asyncio.run(main())

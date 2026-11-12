"""Nationwide accuracy check of the live app's building-register lookup.

1) With the key (DATA_GO_KR_KEY), find real lots that have building records in every
   province (random 시군구·법정동·본번) and fetch the full record list directly.
2) Open the live GitHub Pages app in a real browser, run the same lookups through the
   UI, and compare: record count, the set of (지번, 면적), and the values written by
   [채우기] (연면적·지상·지하·용도).
Only public building data is printed.
"""
import asyncio
import re
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

API = 'https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo'
SITE = 'https://gurifire.github.io/fire-construction/?nat=' + str(int(time.time()))
random.seed(20261112)
KEY = os.environ.get('DATA_GO_KR_KEY', '').strip()


def api(params, page=1):
    key = KEY if '%' in KEY else urllib.parse.quote(KEY, safe='')
    url = API + '?serviceKey=' + key + '&' + urllib.parse.urlencode(
        {**params, '_type': 'json', 'numOfRows': 100, 'pageNo': page})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read().decode('utf-8', 'replace'))
            body = data['response']['body']
            raw = body['items']['item'] if isinstance(body.get('items'), dict) else []
            return (raw if isinstance(raw, list) else [raw]), int(body.get('totalCount') or 0)
        except Exception:
            time.sleep(1 + attempt)
    return None, None


def all_items(params):
    items, total, page = [], 0, 1
    while page <= 5:
        part, total = api(params, page)
        if part is None:
            return None, None
        items += part
        if len(part) < 100 or len(items) >= total:
            break
        page += 1
    return items, total


def find_lots():
    lots = []
    index = json.loads(Path('regions_v1/index.json').read_text(encoding='utf-8'))
    for sido_cd, sido_name in index['sido']:
        data = json.loads(Path(f'regions_v1/{sido_cd}.json').read_text(encoding='utf-8'))
        found = 0
        for _ in range(12):
            if found >= 2:
                break
            sgg_cd, sgg_name = random.choice(data['sgg'])
            dongs = data['dong'].get(sgg_cd) or []
            if not dongs:
                continue
            dong_cd, dong_name = random.choice(dongs)
            for bun in random.sample([1, 2, 5, 10, 12, 20, 33, 50, 77, 100, 120, 150, 200, 250, 300, 400, 500], 6):
                params = {'sigunguCd': sgg_cd, 'bjdongCd': dong_cd, 'platGbCd': '0', 'bun': f'{bun:04d}'}
                items, total = all_items(params)
                if items:
                    lots.append({'label': f'{sido_name} {sgg_name} {dong_name} {bun}', 'sido': sido_cd,
                                 'sgg': sgg_cd, 'dong': sgg_cd + dong_cd, 'bun': str(bun), 'items': items, 'total': total})
                    found += 1
                    break
                time.sleep(0.2)
    return lots


def key_of(it):
    b, j = int(it.get('bun') or 0), int(it.get('ji') or 0)
    area = it.get('totArea')
    area = '' if area in ('', None) else f'{float(area):.2f}'
    return f'{b}' + (f'-{j}' if j else '') + '|' + area


async def check(lots):
    problems = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.goto(SITE, wait_until='networkidle')
        await page.click('#v85-reg>summary')
        await page.wait_for_function("document.getElementById('v91-sido').options.length>1", timeout=20000)
        for lot in lots:
            await page.select_option('#v91-sido', lot['sido'])
            await page.wait_for_function(f"[...document.getElementById('v91-sgg').options].some(o=>o.value==='{lot['sgg']}')", timeout=20000)
            await page.select_option('#v91-sgg', lot['sgg'])
            await page.wait_for_function(f"[...document.getElementById('v85-dong').options].some(o=>o.value==='{lot['dong']}')", timeout=20000)
            await page.select_option('#v85-dong', lot['dong'])
            await page.fill('#v85-ji', '')
            await page.fill('#v85-bun', lot['bun'])
            await page.click('#v85-run')
            await page.wait_for_function("!document.getElementById('v85-run').disabled", timeout=90000)
            msg = await page.evaluate("document.getElementById('v85-msg').textContent")
            cards = await page.evaluate("[...document.querySelectorAll('#v85-list .v85-item')].map(e=>e.innerText)")
            # app items (via the same order the UI renders)
            app_keys = await page.evaluate("""(()=>{const out=[];document.querySelectorAll('#v85-list .v85-item').forEach(c=>{
                const t=c.innerText;const m=t.match(/(\\d+(?:-\\d+)?)번지/);const a=t.match(/([\\d,]+(?:\\.\\d+)?)㎡/);
                out.push((m?m[1]:'')+'|'+(a?Number(a[1].replace(/,/g,'')).toFixed(2):''));});return out;})()""")
            api_keys = sorted(key_of(it) for it in lot['items'])
            ok_count = len(cards) == len(lot['items'])
            ok_set = sorted(app_keys) == api_keys
            # fill check with first card
            fill = ''
            if cards:
                first_text = cards[0]
                await page.locator('#v85-list .v85-item').first.click()
                await page.wait_for_timeout(150)
                vals = await page.evaluate("({area:document.getElementById('area').value,g:document.getElementById('ground').value,b:document.getElementById('basement').value,use:document.getElementById('use').selectedOptions[0]?.text||''})")
                purpose = re.search(r'번지 · ([^·]+?) ·', first_text)
                purpose = purpose.group(1).strip() if purpose else '?'
                fill = f"채우기→ 연면적 {vals['area']} / 지상 {vals['g']} / 지하 {vals['b']} / 주용도 {purpose} → 용도 {vals['use'] or '(비움)'}"
                expect_blank = purpose in ('단독주택', '기타', '주용도 미기재', '야영장시설')
                if expect_blank != (vals['use'] in ('', '선택하세요')):
                    fill += '  ❌ 용도 연결 이상'; ok_set = False
                mm = re.search(r'([\d,]+(?:\.\d+)?)㎡', first_text)
                if mm:
                    shown_area = float(mm.group(1).replace(',', ''))
                    if abs(float(vals['area'] or 0) - shown_area) > 0.01:
                        fill += '  ❌ 면적 불일치'; ok_set = False
            status = 'OK' if (ok_count and ok_set) else 'MISMATCH'
            if status != 'OK':
                problems += 1
            print(f"{status:8s} {lot['label']}: API {len(lot['items'])}건(총 {lot['total']}) / 앱 {len(cards)}건 | {msg[:40]}")
            print(f'          {fill}')
            if status != 'OK':
                print('          API:', api_keys[:8])
                print('          APP:', sorted(app_keys)[:8])
        await browser.close()
    return problems


def main():
    if not KEY:
        print('DATA_GO_KR_KEY missing'); return 2
    lots = find_lots()
    print(f'테스트 지번 {len(lots)}곳 (16개 시·도)')
    problems = asyncio.run(check(lots))
    print(f'\n결과: {len(lots) - problems}/{len(lots)} 일치')
    return 0


if __name__ == '__main__':
    sys.exit(main())

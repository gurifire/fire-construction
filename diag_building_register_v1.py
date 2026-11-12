"""Diagnose building-register lookups (run in GitHub Actions; key from DATA_GO_KR_KEY).

Prints, for each test lot, how many records each query variant returns:
  title-noji  : getBrTitleInfo without ji (app V93 behaviour when 부번 is empty)
  title-0000  : getBrTitleInfo with ji=0000
  recap-0000  : getBrRecapTitleInfo (총괄표제부)
  basis-0000  : getBrBasisOulnInfo (기본개요)
Only public building data is printed; the key is never printed.
"""
import json
import os
import sys
import urllib.parse
import urllib.error
import urllib.request

BASE = 'https://apis.data.go.kr/1613000/BldRgstHubService/'
WORKER = 'https://fire-bldg.kdw8116.workers.dev/'
TESTS = [  # (label, sigunguCd, bjdongCd, bun, ji)
    ('서울 강동구 명일동 94', '11740', '10100', '0094', ''),
    ('서울 중구 태평로1가 31 (서울시청)', '11140', '10300', '0031', ''),
    ('서울 영등포구 여의도동 1 (국회)', '11560', '11000', '0001', ''),
    ('부산 연제구 연산동 1000 (부산시청)', '26470', '10200', '1000', ''),
    ('구리 인창동 289', '41310', '10300', '0289', ''),
    ('구리 인창동 289-29', '41310', '10300', '0289', '0029'),
]
VARIANTS = [
    ('title-noji', 'getBrTitleInfo', False),
    ('title-0000', 'getBrTitleInfo', True),
    ('recap-0000', 'getBrRecapTitleInfo', True),
    ('basis-0000', 'getBrBasisOulnInfo', True),
]


def call(key, op, params):
    service_key = key if '%' in key else urllib.parse.quote(key, safe='')
    url = BASE + op + '?serviceKey=' + service_key + '&' + urllib.parse.urlencode(
        {**params, '_type': 'json', 'numOfRows': 100, 'pageNo': 1})
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            text = response.read().decode('utf-8', 'replace')
    except Exception as error:
        return f'ERROR {type(error).__name__}: {error}', []
    try:
        data = json.loads(text)
    except ValueError:
        return 'NON-JSON ' + text[:160].replace('\n', ' '), []
    header = (data.get('response') or {}).get('header') or {}
    body = (data.get('response') or {}).get('body') or {}
    raw = (body.get('items') or {}).get('item') if isinstance(body.get('items'), dict) else None
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    return f"code={header.get('resultCode')} total={body.get('totalCount')}", items


def main():
    key = os.environ.get('DATA_GO_KR_KEY', '').strip()
    if not key:
        print('DATA_GO_KR_KEY missing')
        return 2
    for label, sgg, bjd, bun, ji in TESTS:
        print(f'\n=== {label}  ({sgg}-{bjd} {int(bun)}{"-" + str(int(ji)) if ji else ""})')
        for name, op, with_ji in VARIANTS:
            params = {'sigunguCd': sgg, 'bjdongCd': bjd, 'platGbCd': '0', 'bun': bun}
            if ji:
                params['ji'] = ji
            elif with_ji:
                params['ji'] = '0000'
            status, items = call(key, op, params)
            sample = '; '.join(
                f"{(it.get('bldNm') or '').strip() or '-'} {(it.get('dongNm') or '').strip()} "
                f"[{it.get('bun')}-{it.get('ji')}] {it.get('mainPurpsCdNm') or ''}".strip()
                for it in items[:4])
            print(f'  {name:11s} {status:22s} n={len(items):3d}  {sample}')
        q = {'sigunguCd': sgg, 'bjdongCd': bjd, 'platGbCd': '0', 'bun': bun}
        if ji:
            q['ji'] = ji
        req = urllib.request.Request(WORKER + '?' + urllib.parse.urlencode(q),
                                     headers={'Origin': 'https://gurifire.github.io', 'User-Agent': 'fire-diag'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                text = response.read().decode('utf-8', 'replace')
                info = f'http={response.status} cache={response.headers.get("X-Cache")}'
        except urllib.error.HTTPError as error:
            text = error.read().decode('utf-8', 'replace'); info = f'http={error.code}'
        except Exception as error:
            text = ''; info = f'ERROR {type(error).__name__}: {error}'
        try:
            body = json.loads(text)['response']['body']
            raw = body['items']['item'] if isinstance(body.get('items'), dict) else []
            n = len(raw if isinstance(raw, list) else [raw])
            print(f'  {"worker":11s} {info} total={body.get("totalCount")} n={n}')
        except Exception:
            print(f'  {"worker":11s} {info} body={text[:200]!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

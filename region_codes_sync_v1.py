"""Build legal-dong (법정동) region files for the in-app building-register lookup.

Source: 행정안전부_행정표준코드_법정동코드 OpenAPI (data.go.kr 15077871)
        https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList
Key:    environment variable DATA_GO_KR_KEY (GitHub Actions secret, never committed)

Output (regions_v1/):
  index.json        {"updated", "source", "sido": [[sido_cd, name], ...]}
  <sido_cd>.json    {"sgg": [[sigungu_cd5, name], ...],
                     "dong": {sigungu_cd5: [[bjdong_cd5, name], ...]}}
bjdong_cd5 = umd_cd + ri_cd, the bjdongCd the building register API expects.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ENDPOINT = 'https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList'
OUT = Path('regions_v1')
SIDO = [
    '서울특별시', '부산광역시', '대구광역시', '인천광역시', '광주광역시', '대전광역시',
    '울산광역시', '세종특별자치시', '경기도', '충청북도', '충청남도', '전라남도',
    '경상북도', '경상남도', '제주특별자치도', '강원특별자치도', '전북특별자치도',
]
ROWS = 1000


class SyncError(Exception):
    pass


def find(node, key):
    """Return the first value stored under key anywhere in a nested JSON value."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = find(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find(value, key)
            if found is not None:
                return found
    return None


def fetch_page(key, sido, page):
    service_key = key if '%' in key else urllib.parse.quote(key, safe='')
    query = urllib.parse.urlencode({
        'pageNo': page, 'numOfRows': ROWS, 'type': 'json', 'locatadd_nm': sido})
    url = f'{ENDPOINT}?ServiceKey={service_key}&{query}'
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read().decode('utf-8', 'replace')
            break
        except Exception as error:  # network hiccup: retry
            last = error
            time.sleep(3 * (attempt + 1))
    else:
        raise SyncError(f'{sido} page {page}: {last}')
    if body.lstrip().startswith('<'):
        raise SyncError(f'{sido}: unexpected XML response (key not approved?): {body[:300]}')
    data = json.loads(body)
    result = find(data, 'RESULT') or {}
    code = str(result.get('resultCode', 'INFO-0'))
    if code not in ('INFO-0', '00', 'INFO-000'):
        if code == 'INFO-3':  # no data
            return [], 0
        raise SyncError(f'{sido}: {code} {result.get("resultMsg", "")}')
    rows = find(data, 'row') or []
    total = int(find(data, 'totalCount') or 0)
    return rows, total


def collect(key, sido):
    rows, page = [], 1
    while True:
        part, total = fetch_page(key, sido, page)
        rows.extend(part)
        if not part or len(rows) >= total:
            return rows
        page += 1
        time.sleep(0.3)


def build(sido_name, rows):
    """Turn API rows into the per-province structure."""
    sgg_names, dongs, sido_cd = {}, {}, None
    for row in rows:
        region = str(row.get('region_cd', '')).strip()
        if len(region) != 10 or not region.isdigit():
            continue
        name = str(row.get('locatadd_nm', '')).strip()
        if not name.startswith(sido_name):
            continue
        sido_cd = sido_cd or region[:2]
        sgg5, umd, ri = region[:5], region[5:8], region[8:10]
        if region[2:5] == '000':
            continue
        rest = name[len(sido_name):].strip()
        if umd == '000':
            sgg_names[sgg5] = rest or sido_name  # 세종: 시군구 단계 없음
            continue
        dongs.setdefault(sgg5, []).append((umd + ri, rest))
    if not sido_cd:
        raise SyncError(f'{sido_name}: no rows')
    sgg_list, dong_map = [], {}
    for sgg5 in sorted(dongs):
        items = dongs[sgg5]
        sgg_name = sgg_names.get(sgg5)
        if not sgg_name:  # derive from the first child's address
            sample = items[0][1].split()
            sgg_name = ' '.join(sample[:-1]) if len(sample) > 1 else sample[0]
        # keep 리 rows; keep a 읍·면 row only when it has no 리 children
        has_ri = {code[:3] for code, _ in items if code[3:] != '00'}
        cleaned = []
        for code, full in sorted(items):
            if code[3:] == '00' and code[:3] in has_ri:
                continue
            label = full[len(sgg_name):].strip() if full.startswith(sgg_name) else full
            cleaned.append([code, label or full])
        sgg_list.append([sgg5, sgg_name])
        dong_map[sgg5] = cleaned
    return sido_cd, {'sgg': sgg_list, 'dong': dong_map}


def main():
    key = os.environ.get('DATA_GO_KR_KEY', '').strip()
    if not key:
        print('DATA_GO_KR_KEY secret is not set.', file=sys.stderr)
        return 2
    OUT.mkdir(exist_ok=True)
    index, problems = [], []
    for sido in SIDO:
        try:
            sido_cd, data = build(sido, collect(key, sido))
        except SyncError as error:
            problems.append(str(error))
            continue
        dong_count = sum(len(v) for v in data['dong'].values())
        if len(data['sgg']) == 0 or dong_count < 10:
            problems.append(f'{sido}: too few rows ({dong_count})')
            continue
        (OUT / f'{sido_cd}.json').write_text(
            json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        index.append([sido_cd, sido])
        print(f'{sido} ({sido_cd}): 시군구 {len(data["sgg"])}, 읍면동·리 {dong_count}')
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        if len(index) < len(SIDO):
            print('Some provinces failed; existing files are kept and index is not updated.',
                  file=sys.stderr)
            return 1
    index.sort()
    meta = {
        'updated': datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d'),
        'source': '행정안전부_행정표준코드_법정동코드 (data.go.kr 15077871)',
        'sido': index,
    }
    (OUT / 'index.json').write_text(
        json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())

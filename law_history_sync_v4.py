"""Collect public historical law sources. App rules are reviewed separately."""
import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from law_source_collect_v1 import CheckError, check_body, clean_source, fetch

START = '19800101'
OUT = Path('law_history_sources_v1')
LAW_IDS = {
    '001630': '소방법',
    '003973': '소방법 시행령',
    '007516': '소방법 시행규칙',
    '009503': '시설 관련 법률',
    '009694': '시설 관련 시행령',
    '009730': '시설 관련 시행규칙',
    '009500': '소방시설공사업법',
    '009695': '소방시설공사업법 시행령',
    '009731': '소방시설공사업법 시행규칙',
}
LEGACY_IDS = {'001630', '003973', '007516'}
DEFAULT_FOUNDING_DAY = '20040530'
FOUNDING_DAY_BY_ID = {
    '009730': '20040707',  # 행정자치부령 제240호
    '009731': '20040707',  # 행정자치부령 제241호
}
BATCH = 30


def listing(law_id, credential):
    found = {}
    total = None
    for page in range(1, 101):
        root = fetch('lawSearch.do', {
            'target': 'eflaw', 'LID': law_id,
            'display': '100', 'page': str(page), 'sort': 'efdes',
        }, credential)
        count = root.findtext('.//totalCnt', '').strip()
        entries = root.findall('.//law')
        if not count.isdecimal() or (total is not None and total != int(count)):
            raise CheckError('연혁 목록 건수를 확인할 수 없거나 조회 중 변경됐습니다.')
        total = int(count)
        if len(entries) != min(100, max(0, total - (page - 1) * 100)):
            raise CheckError('연혁 목록 페이지가 불완전합니다.')
        returned = root.findtext('.//page', '').strip()
        if returned and returned != str(page):
            raise CheckError('요청한 연혁 목록 페이지와 응답이 다릅니다.')
        for entry in entries:
            name = entry.findtext('법령명한글', '').strip()
            returned_id = entry.findtext('법령ID', '').strip()
            if not name or not returned_id.isdecimal() or returned_id.zfill(6) != law_id:
                raise CheckError('목록의 법령명 또는 법령 ID가 요청과 일치하지 않습니다.')
            day = entry.findtext('시행일자', '').strip()
            master = entry.findtext('법령일련번호', '').strip()
            if not re.fullmatch(r'[0-9]{8}', day) or not re.fullmatch(r'[0-9]+', master):
                raise CheckError('대상 법령의 시행일 또는 일련번호가 유효하지 않습니다.')
            try:
                date.fromisoformat(day)
            except ValueError:
                raise CheckError('대상 법령의 시행일이 유효하지 않습니다.') from None
            master = str(int(master))
            key = (master, day)
            if key in found and found[key] != (name, law_id):
                raise CheckError('같은 시행본의 목록 정보가 일치하지 않습니다.')
            found[key] = (name, law_id)
        if page * 100 >= total:
            break
    else:
        raise CheckError('연혁 목록 페이지 한도를 초과했습니다.')
    first = max((day for _, day in found if day <= START), default=START)
    founding_day = FOUNDING_DAY_BY_ID.get(law_id, DEFAULT_FOUNDING_DAY)
    anchor = START if law_id in LEGACY_IDS else founding_day
    has_anchor = any(day <= anchor if law_id in LEGACY_IDS else day == anchor
                     for _, day in found)
    if not has_anchor:
        raise CheckError(f'{anchor} 기준 시행본이 목록에 없습니다.')
    return {key: info for key, info in found.items() if key[1] >= first}


def write_once(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != text.encode('utf-8'):
            raise CheckError('보존된 파일과 동일 경로의 새 내용이 다릅니다.')
        return
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)


def collect(credential, today, report):
    candidates = {}
    for law_id, label in LAW_IDS.items():
        try:
            versions = listing(law_id, credential)
            if candidates.keys() & versions.keys():
                raise CheckError('서로 다른 법령 ID에 동일한 시행본이 반환됐습니다.')
            candidates.update(versions)
        except CheckError as exc:
            report.append((label, str(exc)))

    missing, monitor = [], set()
    latest = {}
    for (master, day), (_, law_id) in candidates.items():
        key = (master, day)
        if not (OUT / 'versions' / f'{master}-{day}.json').is_file():
            missing.append(key)
        if day > today:
            monitor.add(key)
        elif law_id not in latest or day >= latest[law_id]:
            latest[law_id] = day
    monitor.update(key for key, (_, law_id) in candidates.items() if key[1] == latest.get(law_id))
    missing.sort(key=lambda key: (key[1] > START, key[1], key[0]))
    # Keep capacity for backlog while continuing to poll known versions.
    existing = sorted(monitor.difference(missing))
    offset = date.fromisoformat(today).toordinal() * 10 % len(existing) if existing else 0
    scheduled = missing[:BATCH] + (existing[offset:] + existing[:offset])[:10]
    added = 0
    revised = 0
    for master, day in scheduled:
        title, law_id = candidates[master, day]
        try:
            root = fetch('lawService.do', {'target': 'eflaw', 'MST': master, 'efYd': day}, credential)
            articles, annexes = check_body(root, title, day)
            if root.findtext('./기본정보/법령ID', '').strip().zfill(6) != law_id:
                raise CheckError('본문의 법령 ID가 목록과 다릅니다.')
            xml = clean_source(root, credential)
            digest = hashlib.sha256(xml.encode()).hexdigest()
            xml_path = OUT / 'objects' / f'{digest}.xml'
            record = OUT / 'versions' / f'{master}-{day}.json'
            existed = record.is_file()
            snapshot = OUT / 'snapshots' / f'{master}-{day}-{digest}.json'
            fresh = not snapshot.exists()
            write_once(xml_path, xml)
            entry = {
                'name': title, 'master': master, 'effectiveDate': day, 'sha256': digest,
                'source': xml_path.relative_to(OUT).as_posix(), 'articles': articles,
                'annexItems': annexes, 'reviewStatus': 'pending',
            }
            encoded = json.dumps(entry, ensure_ascii=False, indent=2) + '\n'
            if fresh:
                write_once(snapshot, encoded)
                added += 1
                revised += int(existed)
            if not existed:
                write_once(record, encoded)
        except CheckError as exc:
            report.append((f'{title} {day} ({master})', str(exc)))

    remaining = sum(not (OUT / 'versions' / f'{m}-{d}.json').is_file() for m, d in missing)
    return len(candidates), added, revised, remaining


def main():
    credential = os.environ.get('LAW_API_OC', '').strip()
    if not credential:
        raise SystemExit('Actions 비밀값 LAW_API_OC를 확인하세요.')
    report = []
    today = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    count, added, revised, remaining = collect(credential, today, report)
    lines = [
        '## 법령 연혁 수집',
        f'- 조회 후보 {count}건 · 새 원문 {added}건(기존 시행본 응답 변경 {revised}건) · 이번 실행 후 미수집 {remaining}건',
        '- 미수집 수는 검증을 통과한 목록 기준입니다. 목록 오류가 표시되면 수집 완료로 판단하지 않습니다.',
        '- 저장된 원문은 판정 규칙 검토 대기 상태입니다.',
    ]
    lines.extend(f'- {title} — {reason}' for title, reason in report)
    print('\n'.join(lines))
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('\n'.join(lines) + '\n')
    if not count or report:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

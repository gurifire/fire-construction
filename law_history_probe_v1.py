"""Probe historical sources for 1980-01-01; do not publish decision rules."""
import json
import os
import re
from datetime import date
from pathlib import Path

from law_source_collect_v1 import CheckError, check_body, clean_source, fetch

BASELINE = '19800101'
OUTPUT = Path('law_history_probe_output')
NAMES = ('소방법', '소방법시행령', '소방법시행규칙')


def identity(value):
    return re.sub(r'\s+', '', value or '')


def baseline_entry(name, credential):
    candidates = set()
    seen = set()
    page_size = 100
    for page in range(1, 21):
        root = fetch('lawSearch.do', {
            'query': name, 'nw': '2', 'display': str(page_size), 'page': str(page),
        }, credential)
        count = root.findtext('.//totalCnt', '').strip()
        if not count.isdecimal():
            raise CheckError('연혁 목록의 전체 건수를 확인할 수 없습니다.')
        entries = root.findall('.//law')
        if len(entries) != min(page_size, max(0, int(count) - (page-1)*page_size)):
            raise CheckError('연혁 목록 페이지가 불완전합니다.')
        for entry in entries:
            master = entry.findtext('법령일련번호', '')
            if not master.isdecimal() or master in seen:
                raise CheckError('연혁 목록의 일련번호가 누락되거나 중복됐습니다.')
            seen.add(master)
            if identity(entry.findtext('법령명한글')) != identity(name):
                continue
            effective = entry.findtext('시행일자', '')
            if not re.fullmatch(r'[0-9]{8}', effective):
                raise CheckError('시행일자를 확인할 수 없습니다.')
            date.fromisoformat(effective)
            if effective <= BASELINE:
                candidates.add((effective, master))
        if len(seen) == int(count):
            break
    else:
        raise CheckError('연혁 목록이 시험 페이지 한도를 초과했습니다.')
    if not candidates:
        raise CheckError('1980-01-01 이전 또는 당일 시행본을 찾지 못했습니다.')
    effective = max(day for day, _ in candidates)
    masters = {master for day, master in candidates if day == effective}
    if len(masters) != 1:
        raise CheckError('동일 시행일에 여러 본문이 있어 원문 대조가 필요합니다.')
    return effective, masters.pop()


def collect(index, name, credential):
    effective, master = baseline_entry(name, credential)
    body = fetch('lawService.do', {'MST': master}, credential)
    articles, annex_count = check_body(body, name, effective)
    text = clean_source(body, credential)
    filename = f'baseline-{index}.xml'
    (OUTPUT / filename).write_text(text, encoding='utf-8')
    annexes = body.findall('.//별표단위')
    return {
        'name': name, 'status': '본문 확보', 'master': master,
        'effectiveDate': effective, 'articles': articles, 'annexItems': annex_count,
        'annexInline': sum(bool(''.join(n.itertext()).strip()) for a in annexes for n in a.findall('별표내용')),
        'supplements': len(body.findall('.//부칙단위')), 'file': filename,
    }


def main():
    credential = os.environ.get('LAW_API_OC', '').strip()
    if not credential:
        print('저장소 Actions 비밀값 LAW_API_OC를 확인하세요.')
        return 1
    OUTPUT.mkdir(exist_ok=False)
    records = []
    for index, name in enumerate(NAMES, 1):
        try:
            record = collect(index, name, credential)
        except CheckError as error:
            record = {'name': name, 'status': '미확보', 'reason': str(error)}
        except Exception:
            record = {'name': name, 'status': '미확보', 'reason': '응답 구조 또는 파일 처리 오류'}
        records.append(record)
        print(name, record['status'])
    (OUTPUT / 'report.json').write_text(
        json.dumps({'baseline': BASELINE, 'records': records}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return int(any(r['status'] != '본문 확보' for r in records))


if __name__ == '__main__':
    raise SystemExit(main())

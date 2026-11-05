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
    dates_by_master = {}
    page_size = 100
    expected_total = None
    for page in range(1, 21):
        root = fetch('lawSearch.do', {
            'target': 'eflaw',
            'query': f'"{name}"',
            'nw': '1',
            'efYd': f'19000101~{BASELINE}',
            'sort': 'efdes',
            'display': str(page_size),
            'page': str(page),
        }, credential)
        count = root.findtext('.//totalCnt', '').strip()
        if not count.isdecimal():
            raise CheckError('연혁 목록의 전체 건수를 확인할 수 없습니다.')
        total = int(count)
        if expected_total is not None and total != expected_total:
            raise CheckError('조회 도중 연혁 목록의 전체 건수가 달라졌습니다.')
        expected_total = total
        entries = root.findall('.//law')
        if len(entries) != min(page_size, max(0, total - (page - 1) * page_size)):
            raise CheckError(f'연혁 목록 {page}쪽의 응답 건수가 예상과 다릅니다.')
        returned_page = root.findtext('.//page', '').strip()
        if returned_page and returned_page != str(page):
            raise CheckError(f'연혁 목록 {page}쪽 요청에 다른 페이지가 반환됐습니다.')
        for entry in entries:
            if identity(entry.findtext('법령명한글')) != identity(name):
                continue
            effective = entry.findtext('시행일자', '').strip()
            if not re.fullmatch(r'[0-9]{8}', effective):
                raise CheckError(f'연혁 목록 {page}쪽의 대상 법령 시행일자가 누락되거나 잘못됐습니다.')
            try:
                date.fromisoformat(effective)
            except ValueError:
                raise CheckError(f'연혁 목록 {page}쪽의 대상 법령 시행일자가 유효하지 않습니다.') from None
            if effective > BASELINE:
                continue
            master = entry.findtext('법령일련번호', '').strip()
            if not re.fullmatch(r'[0-9]+', master):
                raise CheckError(f'연혁 목록 {page}쪽의 대상 시행본 일련번호가 누락되거나 잘못됐습니다.')
            if master in dates_by_master and dates_by_master[master] != effective:
                raise CheckError(f'연혁 목록 {page}쪽의 같은 일련번호에 서로 다른 시행일이 있습니다.')
            dates_by_master[master] = effective
            candidates.add((effective, master))
        if page * page_size >= total:
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
    body = fetch('lawService.do', {
        'target': 'eflaw', 'MST': master, 'efYd': effective,
    }, credential)
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

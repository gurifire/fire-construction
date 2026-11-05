"""Read-only LAW OPEN DATA connection check. Never edits app/rules or logs OC."""
import datetime as dt
import os
import re
import sys
import socket
import ssl
import errno
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

LAWS = ('소방시설 설치 및 관리에 관한 법률 시행령', '소방시설공사업법 시행령')
BASE = 'https://www.law.go.kr/DRF/'
MAX_BYTES = 8 * 1024 * 1024

class CheckError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError('REDIRECT: API 주소가 다른 주소로 이동했습니다. 공식 요청 주소를 확인하세요.')

def parse_xml(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise CheckError('FORMAT: 예상하지 못한 XML 선언입니다.')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise CheckError('FORMAT: XML 응답이 아닙니다. 인증값·승인 서비스·접속 제한을 확인하세요.') from None
    if root.tag.lower() in ('html', 'error', 'errors'):
        raise CheckError('API: 정상 법령 응답이 아닙니다. 인증값·승인 서비스·접속 제한을 확인하세요.')
    return root

def network_message(error):
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        return 'TLS_CERT: HTTPS 인증서 검증 실패. 인증값 문제가 아닌 인증서 연결 오류입니다.'
    if isinstance(cause, ssl.SSLError):
        reason = getattr(cause, 'reason', '')
        if reason == 'UNSAFE_LEGACY_RENEGOTIATION_DISABLED':
            return 'TLS_LEGACY: 서버의 구형 TLS 재협상 방식과 실행 환경이 호환되지 않습니다.'
        return 'TLS_HANDSHAKE: HTTPS 보안 연결 협상에 실패했습니다.'
    if isinstance(cause, socket.gaierror):
        return 'DNS: 법령 서버의 주소를 찾지 못했습니다.'
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return 'TIMEOUT: API 서버 응답 또는 연결 시간이 초과되었습니다.'
    if isinstance(cause, OSError):
        if cause.errno == errno.ECONNREFUSED:
            return 'TCP_REFUSED: 서버가 연결을 거부했습니다.'
        if cause.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return 'TCP_ROUTE: 실행 환경에서 법령 서버로 연결할 수 없습니다.'
        if cause.errno == errno.ECONNRESET:
            return 'TCP_RESET: 연결이 서버 또는 중간 네트워크에서 끊겼습니다.'
    return 'NETWORK_OTHER: 기타 네트워크 오류입니다. 인증값·요청 URL은 출력하지 않습니다.'

def public_network_probe():
    # No credential is sent. Check DNS, TCP, and TLS separately.
    host = 'www.law.go.kr'
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        return '사전 연결 진단: ' + network_message(error)
    try:
        with socket.create_connection((host, 443), timeout=12) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host):
                pass
    except OSError as error:
        return '사전 연결 진단: DNS 성공 / ' + network_message(error)
    return '사전 연결 진단: DNS·TCP·HTTPS 인증서 검증 성공 (API 인증 성공을 뜻하지 않습니다).'

def fetch(endpoint, params, oc):
    query = urllib.parse.urlencode({'OC': oc, 'target': 'law', 'type': 'XML', **params})
    req = urllib.request.Request(BASE + endpoint + '?' + query, headers={'User-Agent': 'FireFacilityReview-ConnectionCheck/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise CheckError('HTTP ' + str(e.code) + ': 인증·승인 상태 또는 API 접근 제한을 확인하세요.') from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CheckError(network_message(error)) from None
    if len(raw) > MAX_BYTES:
        raise CheckError('SIZE: 응답 크기가 시험 범위를 초과했습니다.')
    return parse_xml(raw)

def normalized(text):
    return re.sub(r'\s+', '', text or '')

def select_law(root, name, today):
    found = []
    for law in root.findall('.//law'):
        if normalized(law.findtext('법령명한글')) != normalized(name):
            continue
        effective = law.findtext('시행일자', '')
        master = law.findtext('법령일련번호', '')
        if not re.fullmatch(r'\d{8}', effective) or not re.fullmatch(r'\d+', master):
            continue
        try:
            dt.datetime.strptime(effective, '%Y%m%d')
        except ValueError:
            continue
        if effective <= today:
            found.append((effective, int(master), master))
    if not found:
        raise CheckError('SEARCH: 정확히 일치하는 시행 중 법령을 찾지 못했습니다. 검색 응답·API 권한을 확인하세요.')
    effective, _, master = max(found)
    return master, effective

def check_body(root, name, effective):
    if normalized(root.findtext('.//법령명_한글')) != normalized(name):
        raise CheckError('BODY: 조회한 본문의 법령명이 검색 결과와 다릅니다.')
    if root.findtext('.//기본정보/시행일자') != effective:
        raise CheckError('BODY: 검색 결과와 본문의 시행일자가 다릅니다.')
    articles = root.findall('.//조문단위')
    if not any(''.join(a.itertext()).strip() and a.findtext('조문내용', '').strip() for a in articles):
        raise CheckError('BODY: 본문 조문을 확인하지 못했습니다. 본문 조회 서비스 승인을 확인하세요.')
    return len(articles), len(root.findall('.//별표단위'))

# Step 2: collect sanitized API XML and compare source hashes. No rule changes.
import hashlib
import json
import difflib
from pathlib import Path

STORE = Path('law_sources_v1')

def clean_source(root, oc):
    text = ET.tostring(root, encoding='unicode')
    # API responses can include authenticated links. Remove only OC query values.
    text = re.sub(r'(?i)([?&](?:amp;)?OC=)[^&\s"<>]*', r'\1REDACTED', text)
    # Fail closed rather than publishing a response that echoes the credential elsewhere.
    if oc in text or urllib.parse.quote(oc, safe='') in text:
        raise CheckError('SECRET_ECHO: 응답에 인증값이 남아 있어 저장을 중단했습니다.')
    return ET.canonicalize(text)

def annex_status(root):
    answer = {}
    for number in (4, 5, 6):
        found = [a for a in root.findall('.//별표단위')
                 if a.findtext('별표번호', '').strip().lstrip('0') == str(number)
                 and a.findtext('별표가지번호', '0').strip() in ('', '0', '00', '000')
                 and a.findtext('별표구분', '').strip() == '별표']
        # If the API uses a different classification, report it as unconfirmed.
        inline = any(''.join(a.find('별표내용').itertext()).strip()
                     for a in found if a.find('별표내용') is not None)
        answer[str(number)] = '본문 포함' if inline else '항목 확인·내용 별도 확인' if found else '항목 미확인'
    return answer

def main():
    oc = os.environ.get('LAW_API_OC', '').strip()
    if not oc:
        raise CheckError('SECRET: LAW_API_OC를 확인하세요.')
    stamp = dt.datetime.now(ZoneInfo('Asia/Seoul'))
    today = stamp.strftime('%Y%m%d')
    previous = None
    if STORE.exists():
        records = sorted(STORE.glob('*/snapshot.json'))
        if records:
            previous = json.loads(records[-1].read_text(encoding='utf-8'))
    collected = []
    contents = {}
    for index, name in enumerate(LAWS):
        listing = fetch('lawSearch.do', {'query': name, 'display': '100', 'page': '1'}, oc)
        master, effective = select_law(listing, name, today)
        body = fetch('lawService.do', {'MST': master}, oc)
        articles, annexes = check_body(body, name, effective)
        content = clean_source(body, oc)
        filename = 'installation-decree.xml' if index == 0 else 'construction-decree.xml'
        digest = hashlib.sha256(content.encode()).hexdigest()
        prior = next((r for r in (previous or {}).get('laws', []) if r['name'] == name), None)
        state = '최초 기준 저장' if prior is None else '원문 변경 감지' if digest != prior['sha256'] else '변경 없음'
        collected.append({'name': name, 'master': master, 'effectiveDate': effective,
                          'sha256': digest, 'file': filename, 'articles': articles,
                          'annexItems': annexes, 'annex456': annex_status(body) if index == 0 else {}, 'result': state})
        contents[filename] = content
    changed = previous is None or any(r['result'] != '변경 없음' for r in collected)
    # Write only after BOTH APIs passed validation. Existing snapshots are immutable.
    if changed:
        digest = hashlib.sha256(''.join(r['sha256'] for r in collected).encode()).hexdigest()[:12]
        folder = STORE / (stamp.strftime('%Y%m%dT%H%M%S%f') + '-' + digest)
        folder.mkdir(parents=True, exist_ok=False)
        try:
            for filename, text in contents.items():
                (folder / filename).write_text(text, encoding='utf-8')
            report = {'schemaVersion': 1, 'collectedAt': stamp.isoformat(),
                      'calculationRulesChanged': False, 'laws': collected}
            (folder / 'snapshot.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if previous:
                old_folder = records[-1].parent
                diff = []
                for filename, text in contents.items():
                    old = (old_folder / filename).read_text(encoding='utf-8')
                    # Separate XML tags for readable source diffs.
                    diff.extend(difflib.unified_diff(old.replace('><', '>\n<').splitlines(), text.replace('><', '>\n<').splitlines(), fromfile='previous/'+filename, tofile='current/'+filename, lineterm=''))
                (folder / 'source-diff.txt').write_text('\n'.join(diff), encoding='utf-8')
        except Exception:
            import shutil
            shutil.rmtree(folder)
            raise
    lines = ['## 법령 원문 수집 결과', '', '| 법령 | 시행일 | 결과 |', '|---|---|---|']
    for r in collected:
        lines.append('| '+r['name']+' | '+r['effectiveDate']+' | '+r['result']+' |')
    lines += ['', '### 설치법 시행령 별표 4·5·6 응답 확인']
    for number, status in collected[0]['annex456'].items():
        lines.append('- 별표 '+number+': '+status)
    lines += ['', '원문 변경 감지는 법령 개정의 최종 판정이 아닙니다. 링크·서식 변화도 포함될 수 있습니다.',
              '별표 항목·내용 포함 표시는 API 응답 수준의 확인입니다. PDF/HWP 파일·이미지 내용의 완전성은 후속 확인이 필요합니다.',
              '앱의 계산 기준은 변경하지 않았습니다. 저장소 반영 여부는 다음 Save collected sources 단계까지 성공했는지 확인하세요.']
    message = '\n'.join(lines) + '\n'
    print(message)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(message)
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except CheckError as error:
        print(str(error))
        sys.exit(1)
    except Exception:
        print('COLLECT_ERROR: 원문 수집·검증·저장 중 오류가 발생했습니다. 인증값·원문은 로그에 출력하지 않습니다.')
        sys.exit(1)

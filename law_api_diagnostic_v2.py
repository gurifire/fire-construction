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

def main():
    oc = os.environ.get('LAW_API_OC', '').strip()
    if not oc:
        raise CheckError('SECRET: 저장소 Secrets에 LAW_API_OC를 등록하세요.')
    today = dt.datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    lines = ['## 법령 API 연결 진단 v2', '', public_network_probe(), '', '| 법령 | 시행일 | 목록 | 본문 |', '|---|---|---|---|']
    failed = False
    for name in LAWS:
        try:
            listing = fetch('lawSearch.do', {'query': name, 'display': '100', 'page': '1'}, oc)
            master, effective = select_law(listing, name, today)
            body = fetch('lawService.do', {'MST': master}, oc)
            articles, annexes = check_body(body, name, effective)
            lines.append(f'| {name} | {effective} | 성공 | 성공: 조문 {articles}개·별표 항목 {annexes}개 |')
        except CheckError as error:
            failed = True
            # Only fixed messages are emitted. Never print raw response, URLs, or exception repr.
            lines.append(f'| {name} | — | 확인 필요 | {error} |')
    lines += ['', '이 시험은 목록·본문 접근만 확인합니다. 법령 해석·별표 내용의 완전성·계산 기준 검증 또는 앱 자동 갱신을 수행하지 않습니다.']
    report = '\n'.join(lines) + '\n'
    print(report)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(report)
    return 1 if failed else 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except CheckError as error:
        print(str(error))
        sys.exit(1)
    except Exception:
        print('INTERNAL: 시험 처리 중 오류가 발생했습니다. 원문·인증값은 출력하지 않습니다.')
        sys.exit(1)

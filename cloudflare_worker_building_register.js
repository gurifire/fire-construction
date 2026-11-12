/*
 * 건축물대장 조회 중간 서버 (Cloudflare Worker) — 5판
 * - 인증키는 Cloudflare 비밀값 DATA_GO_KR_KEY 에만 저장 (이 파일·저장소에는 없음)
 * - 우리 앱 주소(ALLOWED_ORIGINS)에서 온 요청만 받음
 * - 표제부(getBrTitleInfo) 조회만 중계. 부번(ji)을 비우면 그 본번의 모든 부번 조회
 * - 결과가 100건을 넘으면 최대 5쪽(500건)까지 이어 받아 합침
 * - 정상 결과(resultCode 00)만 저장 (workers.dev 주소에서는 Cloudflare 정책상 저장이 동작하지 않을 수 있음)
 * - 공공데이터 서버 일시 장애(5xx·깨진 응답·연결 실패)는 최대 3번 시도, 그래도 실패하면 UPSTREAM_BUSY
 * - 0건이면 2번 더 확인 (지역 서버에 따라 빈 결과가 오는 경우 대비)
 * - 확인용 헤더: X-Colo(실행 지역), X-Attempts(시도 횟수), X-Total
 * - 5판: ?mode=juso&keyword=… 도로명·건물명 주소 검색 (행정안전부 검색API, 비밀값 JUSO_KEY)
 * 설치: Cloudflare → Workers → fire-bldg → 코드 편집 → 전체 교체 → Deploy
 *       (비밀값 DATA_GO_KR_KEY 는 이미 넣었다면 그대로 유지)
 */
const ALLOWED_ORIGINS = ['https://gurifire.github.io'];
const UPSTREAM = 'https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo';
const REQUIRED = { sigunguCd: /^\d{5}$/, bjdongCd: /^\d{5}$/, platGbCd: /^[012]$/, bun: /^\d{4}$/ };
const PAGE = 100, MAX_PAGES = 5, CACHE_SECONDS = 3600;
const JUSO_API = 'https://business.juso.go.kr/addrlink/addrLinkApi.do';

/* 도로명·건물명 검색 → 지번(법정동코드·본번·부번·산) 목록 */
async function handleJuso(input, env, jsonHeaders) {
  if (!env.JUSO_KEY) return new Response(JSON.stringify({ error: 'JUSO_KEY_MISSING' }), { status: 500, headers: jsonHeaders });
  const keyword = (input.get('keyword') || '').replace(/[%=><'"`;\\()\[\]{}|^*+?$]/g, ' ').replace(/\s+/g, ' ').trim();
  if (keyword.length < 2 || keyword.length > 60) return new Response(JSON.stringify({ error: 'BAD_KEYWORD' }), { status: 400, headers: jsonHeaders });
  const q = new URLSearchParams({ confmKey: env.JUSO_KEY.trim(), currentPage: '1', countPerPage: '30', keyword, resultType: 'json' });
  let data = null, lastErr = '';
  for (let attempt = 0; attempt < 3 && !data; attempt++) {
    try {
      const res = await fetch(JUSO_API + '?' + q.toString());
      const text = await res.text();
      if (res.status < 500 && /^\s*\{/.test(text)) data = JSON.parse(text); else lastErr = 'HTTP ' + res.status;
    } catch (e) { lastErr = 'network'; }
    if (!data) await new Promise(r => setTimeout(r, 400 * (attempt + 1)));
  }
  if (!data) return new Response(JSON.stringify({ error: 'UPSTREAM_BUSY', detail: lastErr }), { status: 503, headers: jsonHeaders });
  const common = (data.results && data.results.common) || {};
  if (common.errorCode && common.errorCode !== '0') {
    return new Response(JSON.stringify({ error: 'JUSO_' + common.errorCode, message: common.errorMessage || '' }), { status: 200, headers: jsonHeaders });
  }
  const list = ((data.results && data.results.juso) || []).map(j => ({
    roadAddr: j.roadAddr, jibunAddr: j.jibunAddr, bdNm: j.bdNm, admCd: j.admCd,
    bun: j.lnbrMnnm, ji: j.lnbrSlno, san: j.mtYn, apt: j.bdKdcd,
  }));
  return new Response(JSON.stringify({ total: Number(common.totalCount || list.length), juso: list }), { status: 200, headers: jsonHeaders });
}

const AUTH_ERROR = /SERVICE_KEY|LIMITED_NUMBER|UNAUTHORIZED|등록되지 않은/i;
/* 공공데이터 서버는 가끔(측정 약 17%) 503·깨진 응답·연결 끊김을 냄 → 최대 3번 시도 */
async function fetchRetry(url) {
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url);
      const text = await res.text();
      const json = /^\s*\{/.test(text);
      if (res.status < 500 && (json || AUTH_ERROR.test(text))) return { status: res.status, text, type: res.headers.get('Content-Type') };
      last = { status: res.status, text, type: res.headers.get('Content-Type') };
    } catch (e) { last = null; }
    await new Promise(r => setTimeout(r, 400 * (attempt + 1)));
  }
  return last ? { ...last, failed: true } : { status: 0, text: '', failed: true };
}

function itemsOf(data) {
  const raw = data && data.response && data.response.body && data.response.body.items && data.response.body.items.item;
  return raw ? (Array.isArray(raw) ? raw : [raw]) : [];
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get('Origin') || '';
    const allowed = ALLOWED_ORIGINS.includes(origin);
    const cors = {
      'Access-Control-Allow-Origin': allowed ? origin : ALLOWED_ORIGINS[0],
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Vary': 'Origin',
      'Access-Control-Expose-Headers': 'X-Colo, X-Attempts, X-Total, X-Cache',
    };
    const colo = (request.cf && request.cf.colo) || '';
    let attempts = 0;
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'GET' || !allowed) return new Response('forbidden', { status: 403, headers: cors });

    const input = new URL(request.url).searchParams;
    if (input.get('mode') === 'juso') return handleJuso(input, env, { ...cors, 'Content-Type': 'application/json; charset=utf-8' });
    if (!env.DATA_GO_KR_KEY) return new Response('server key missing', { status: 500, headers: cors });
    const query = new URLSearchParams();
    for (const [name, rule] of Object.entries(REQUIRED)) {
      const value = input.get(name) || '';
      if (!rule.test(value)) return new Response('bad request: ' + name, { status: 400, headers: cors });
      query.set(name, value);
    }
    const ji = input.get('ji') || '';
    if (ji && !/^\d{4}$/.test(ji)) return new Response('bad request: ji', { status: 400, headers: cors });
    if (ji) query.set('ji', ji);

    const jsonHeaders = { ...cors, 'Content-Type': 'application/json; charset=utf-8' };
    const cache = typeof caches !== 'undefined' ? caches.default : null;
    const cacheKey = new Request('https://cache.fire-bldg/v2?' + query.toString());
    if (cache) {
      const hit = await cache.match(cacheKey);
      if (hit) return new Response(hit.body, { status: 200, headers: { ...jsonHeaders, 'X-Cache': 'HIT' } });
    }

    const key = env.DATA_GO_KR_KEY.trim();
    const serviceKey = /%[0-9A-Fa-f]{2}/.test(key) ? key : encodeURIComponent(key);
    let all = [], total = 0, first = null;
    for (let page = 1; page <= MAX_PAGES; page++) {
      const q = new URLSearchParams(query);
      q.set('_type', 'json'); q.set('numOfRows', String(PAGE)); q.set('pageNo', String(page));
      let upstream = await fetchRetry(UPSTREAM + '?serviceKey=' + serviceKey + '&' + q.toString());
      attempts++;
      if (upstream.failed) {
        const busy = { response: { header: { resultCode: 'UPSTREAM_BUSY', resultMsg: '공공데이터 서버 일시 장애 (' + (upstream.status || '연결 실패') + ')' } } };
        return new Response(JSON.stringify(busy), { status: 503, headers: jsonHeaders });
      }
      const text = upstream.text;
      let data;
      try { data = JSON.parse(text); } catch (e) {
        // 인증 오류 등은 XML로 옴: 그대로 전달 (저장하지 않음)
        return new Response(text, { status: upstream.status, headers: { ...cors, 'Content-Type': upstream.type || 'text/plain; charset=utf-8' } });
      }
      const head = (data.response && data.response.header) || {};
      if (head.resultCode && head.resultCode !== '00') {
        return new Response(text, { status: 200, headers: jsonHeaders });
      }
      let items = itemsOf(data);
      for (let again = 0; page === 1 && items.length === 0 && again < 2; again++) {
        await new Promise(r => setTimeout(r, 500));
        const retry = await fetchRetry(UPSTREAM + '?serviceKey=' + serviceKey + '&' + q.toString());
        attempts++;
        if (retry.failed) continue;
        try { const d2 = JSON.parse(retry.text); if (itemsOf(d2).length) { data = d2; items = itemsOf(d2); } } catch (e) { /* keep empty */ }
      }
      first = first || data;
      total = Number((data.response && data.response.body && data.response.body.totalCount) || items.length);
      all = all.concat(items);
      if (items.length < PAGE || all.length >= total) break;
    }
    const merged = {
      response: {
        header: (first && first.response && first.response.header) || { resultCode: '00' },
        body: { items: all.length ? { item: all } : '', totalCount: total, returned: all.length },
      },
    };
    const body = JSON.stringify(merged);
    if (cache) {
      ctx.waitUntil(cache.put(cacheKey, new Response(body, { headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=' + CACHE_SECONDS } })));
    }
    return new Response(body, { status: 200, headers: { ...jsonHeaders, 'X-Cache': 'MISS', 'X-Colo': colo, 'X-Attempts': String(attempts), 'X-Total': String(total) } });
  },
};

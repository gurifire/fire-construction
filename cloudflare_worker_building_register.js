/*
 * 건축물대장 조회 중간 서버 (Cloudflare Worker) — 2판
 * - 인증키는 Cloudflare 비밀값 DATA_GO_KR_KEY 에만 저장 (이 파일·저장소에는 없음)
 * - 우리 앱 주소(ALLOWED_ORIGINS)에서 온 요청만 받음
 * - 표제부(getBrTitleInfo) 조회만 중계. 부번(ji)을 비우면 그 본번의 모든 부번 조회
 * - 결과가 100건을 넘으면 최대 5쪽(500건)까지 이어 받아 합침
 * - 정상 결과(resultCode 00)만 1시간 저장. 오류 응답은 저장하지 않음
 * 설치: Cloudflare → Workers → fire-bldg → 코드 편집 → 전체 교체 → Deploy
 *       (비밀값 DATA_GO_KR_KEY 는 이미 넣었다면 그대로 유지)
 */
const ALLOWED_ORIGINS = ['https://gurifire.github.io'];
const UPSTREAM = 'https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo';
const REQUIRED = { sigunguCd: /^\d{5}$/, bjdongCd: /^\d{5}$/, platGbCd: /^[012]$/, bun: /^\d{4}$/ };
const PAGE = 100, MAX_PAGES = 5, CACHE_SECONDS = 3600;

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
    };
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'GET' || !allowed) return new Response('forbidden', { status: 403, headers: cors });
    if (!env.DATA_GO_KR_KEY) return new Response('server key missing', { status: 500, headers: cors });

    const input = new URL(request.url).searchParams;
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
      const upstream = await fetch(UPSTREAM + '?serviceKey=' + serviceKey + '&' + q.toString());
      const text = await upstream.text();
      let data;
      try { data = JSON.parse(text); } catch (e) {
        // 인증 오류 등은 XML로 옴: 그대로 전달 (저장하지 않음)
        return new Response(text, { status: upstream.status, headers: { ...cors, 'Content-Type': upstream.headers.get('Content-Type') || 'text/plain; charset=utf-8' } });
      }
      const head = (data.response && data.response.header) || {};
      if (head.resultCode && head.resultCode !== '00') {
        return new Response(text, { status: 200, headers: jsonHeaders });
      }
      first = first || data;
      const items = itemsOf(data);
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
    return new Response(body, { status: 200, headers: { ...jsonHeaders, 'X-Cache': 'MISS' } });
  },
};

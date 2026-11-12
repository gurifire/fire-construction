/*
 * 건축물대장 조회 중간 서버 (Cloudflare Worker)
 * - 인증키는 Cloudflare 비밀값 DATA_GO_KR_KEY 에만 저장 (이 파일·저장소에는 없음)
 * - 우리 앱 주소(ALLOWED_ORIGINS)에서 온 요청만 받음
 * - 표제부(getBrTitleInfo) 조회만 중계, 같은 지번은 1시간 캐시
 * 설치: Cloudflare → Workers → Create → 이 코드 붙여넣기 → Deploy
 *       → Settings → Variables and Secrets → Add → Secret, 이름 DATA_GO_KR_KEY
 */
const ALLOWED_ORIGINS = ['https://gurifire.github.io'];
const UPSTREAM = 'https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo';
const FIELDS = { sigunguCd: /^\d{5}$/, bjdongCd: /^\d{5}$/, platGbCd: /^[012]$/, bun: /^\d{4}$/, ji: /^\d{4}$/ };

export default {
  async fetch(request, env) {
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
    for (const [name, rule] of Object.entries(FIELDS)) {
      const value = input.get(name) || '';
      if (!rule.test(value)) return new Response('bad request: ' + name, { status: 400, headers: cors });
      query.set(name, value);
    }
    query.set('_type', 'json');
    query.set('numOfRows', '100');
    query.set('pageNo', '1');

    const key = env.DATA_GO_KR_KEY.trim();
    const serviceKey = /%[0-9A-Fa-f]{2}/.test(key) ? key : encodeURIComponent(key);
    const upstream = await fetch(UPSTREAM + '?serviceKey=' + serviceKey + '&' + query.toString(), {
      cf: { cacheTtl: 3600, cacheEverything: true },
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        ...cors,
        'Content-Type': upstream.headers.get('Content-Type') || 'application/json; charset=utf-8',
        'Cache-Control': 'public, max-age=3600',
      },
    });
  },
};

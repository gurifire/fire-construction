"""법령 개정 자동 반영 v1 — 시행령 별표 4(설치대상 소방시설) 개정 감지·분석·앱 반영.

동작
 1) 수집된 시행령 원문(law_sources_v1 최신본 + law_history_sources_v1 공포·예정본)에서
    기준본(law_auto_v1/baseline.json)보다 시행일이 늦은 판을 찾는다.
 2) 별표 4를 설치기준(면적·층수·용도) 구조로 분석해 기준본과 시설별로 비교한다.
 3) 기준이 바뀐 판이 있으면 index.html의 LAW-AUTO 블록에 반영한다.
    앱은 시행일이 되면 그 판을 자동으로 쓰고, 바뀐 시설은 새 기준으로 판정한다.
 4) 분석 결과가 비정상(시설 수 급감 등)이면 반영하지 않고 보류 사유만 남긴다.
인증값은 읽지도 출력하지도 않는다(수집은 별도 워크플로가 담당).
"""
import glob, hashlib, html, json, os, re, sys, datetime as dt
import xml.etree.ElementTree as ET

ROOT = os.environ.get('LAW_AUTO_ROOT', '.')
APP = os.path.join(ROOT, os.environ.get('LAW_AUTO_APP', 'index.html'))
OUT = os.path.join(ROOT, 'law_auto_v1')
LAW = '소방시설 설치 및 관리에 관한 법률 시행령'
BEGIN, END = '<!--LAW-AUTO-BEGIN-->', '<!--LAW-AUTO-END-->'

GA='가나다라마바사아자차카타파하'
A=r'([\d,만천백 ]+?)\s*(?:제곱미터|㎡)\s*(?:\((?:[^()]|\([^()]*\))*\)\s*)?(?:이상|(?<=\))(?=이거나))'
def num(s):
    s=s.replace(',','').replace(' ','')
    if '만' in s:
        a,b=s.split('만',1); return int(a or 1)*10000+(num(b) if b else 0)
    if s.isdigit(): return int(s)
    m=re.match(r'^(?:(\d*)천)?(?:(\d*)백)?(\d*)$',s)
    if not m: return None
    a,b,c=m.groups();tot=0
    if a is not None: tot+=int(a or 1)*1000
    if b is not None: tot+=int(b or 1)*100
    if c: tot+=int(c)
    return tot
def join(prev,st):
    return prev+('' if re.search(r'[가-힣\d]$',prev) and re.match(r'[가-힣]',st) else ' ')+st
import html
def clean_lines(text):
    out=[]
    for ln in html.unescape(text).replace('\r','').split('\n'):
        if '│' in ln: ln=ln.split('│',1)[1]
        ln=ln.replace('┃','').strip()
        if not ln or set(ln)<=set('━┯┓┏┠─┼┨┗┷┛│┃ '): continue
        out.append(ln)
    return out
def tree(text):
    lines=clean_lines(text)
    new=any(re.match(r'^\d+\)\s',l) for l in lines)
    pats=[(1,r'^(\d+)\.\s*(.+)$'),(2,r'^([가-힣])\.\s*(.+)$'),(3,r'^(\d+)\)\s*(.+)$'),(4,r'^([가-힣])\)\s*(.+)$')] if new else [(1,r'^(\d+)\.\s*(.+)$'),(2,r'^([가-힣])\.\s*(.+)$'),(3,r'^\((\d+)\)\s*(.+)$')]
    root={'t':'','c':[],'new':new};stack=[(0,root)];cur=None;started=False
    for st in lines:
        if re.match(r'^\[별표',st): continue
        if re.match(r'^(?:비고|※)',st) and started: break
        lv=None
        for L,p in pats:
            m=re.match(p,st)
            if m and (L not in (2,4) or m.group(1) in GA) and not re.match(r'^(?:은|는|을|를|이|가|의|에|와|과|로|으로)(?:\s|$)',m.group(2)):
                lv,txt,key=L,m.group(2),m.group(1);break
        if not lv and started and re.match(r'^(?:상수도소화용수설비|소화수조)[가-힣\s]{0,12}(?:을|를)\s*설치(?:하여|해)야 하는',st):
            lv,txt,key=1,st,'?'
        if lv:
            started=True
            while stack[-1][0]>=lv: stack.pop()
            node={'t':txt,'c':[],'k':key};stack[-1][1]['c'].append(node);stack.append((lv,node));cur=node
        elif cur: cur['t']=join(cur['t'],st)
    return root
QUAL=re.compile(r'중(?:\s|$)|한정|에 한한다|에 한함|있는|가능한|해당하는')
def strip_paren(t):
    def f(m):
        inner=m.group(0)
        if re.search(r'한정|한한다|만 해당|에 해당하는 것만',inner): return ' 중 '
        mm=re.match(r'^\((.+?)(?:은|는|을|를)\s*제외한다\)$',inner)
        if mm and not re.search(r'\d|층|면적|경우',mm.group(1)):
            ws=[w.strip() for w in re.split(r'[·ㆍ․,]|\s?및\s|\s?또는\s',mm.group(1)) if w.strip()]
            return '!'+'|'.join(re.sub(r'\s','',w) for w in ws)+'!'
        return ''
    prev=None
    while prev!=t:
        prev=t;t=re.sub(r'\([^()]*\)',f,t)
    return t
def split_uses(t,known):
    t=re.sub(r'^.*?(?:\)|호|목)(?:에|의)?\s*해당하지\s*(?:않는|아니하는)\s*','',t.strip())
    t=re.sub(r'^(?:그 밖의|기타)\s*','',t)
    t=strip_paren(t)
    seps=r',\s*|\s?또는\s?|이나\s' if ',' in t else r'[·ㆍ․]|\s?또는\s?|이나\s'
    kn=set(known)
    out=[]
    for frag in re.split(seps,t):
        frag=frag.strip()
        if not frag: continue
        nf=re.sub(r'\s','',frag)
        nf0=re.sub(r'!.*?!','',nf)
        parts=[frag] if (not re.search(r'\s?및\s',frag) or any(nf0==k or (nf0.startswith(k) and nf0[len(k):].startswith('중')) for k in kn)) else re.split(r'\s?및\s',frag)
        for p in parts:
            q=bool(QUAL.search(p))
            ex=re.findall(r'!(.*?)!',p)
            p2=re.sub(r'!.*?!','',p)
            p2=re.sub(r'(?:으로서|로서).*$','',p2).strip().rstrip(' 중').strip()
            if p2: out.append(('~' if q else '')+p2+(('!'+'|'.join(ex)) if ex else ''))
    return out
def classify(t,known,subs=None):
    t=t.strip()
    if re.match(r'^삭제',t): return []
    if re.search(r'별표\s*2의\s*특정소방대상물에\s*설치|모든\s*특정소방대상물에\s*설치',t): return [{'t':'all'}]
    if subs and re.search(r'(?:은|는)\s*다음의 어느 하나에 해당하는 (?:특정소방대상물|것)에 설치',t):
        return [{'t':'uses','u':[x for s in subs for x in split_uses(re.sub(r'[.。]$','',s['t']),known)]}]
    t=re.sub(r'^.*?에 해당하지\s*(?:않는|아니하는)\s*특정소방대상물(?:로서|으로서)\s*','',t)
    mm=re.match(r'^(.+?)의 경우에는 모든 층\.?$',t)
    if mm and not re.search(r'\d|면적|층수',mm.group(1)): return [{'t':'uses','u':split_uses(mm.group(1),known)}]
    tot=bool(re.search(r'지하층을 포함하는 층수',t))
    res=classify2(t,known,subs)
    for c in res:
        if tot and c['t'] in ('area','ground') and c.get('g'): c['tot']=True
        if c.get('u') and any('특정소방대상물' in x for x in c['u']): c['u']=None
        if c['t']=='uses' and not c.get('u'): c['t']='unknown';c['u']=None
    return res
def classify2(t,known,subs=None):
    pre=re.match(r'^(.+?)(?:로서|으로서)\s*다음의 어느 하나에 해당하는',t)
    if subs and re.search(r'다음의 어느 하나에 해당하는 용도로 사용되는 (?:시설|부분)의 바닥면적의 합계가\s*'+A,t):
        a=num(re.search(A,t).group(1))
        return [{'t':'area','u':['~'+x if QUAL.search(x) else x for x in [re.sub(r'\(.*?\)','',s['t']).strip() for s in subs]],'a':a,'alt':False,'b':1}]
    if subs and pre:
        u=split_uses(pre.group(1),known);res=[]
        for s in subs:
            for c in classify(s['t'],known):
                if c['t'] in ('area','ground') and not c.get('u'): c['u']=u
                elif c['t']=='unknown': c['u']=u
                res.append(c)
        return res or [{'t':'unknown','u':u}]
    m=re.match(r'^(.+?)(?:로서|으로서)\s*(?:해당 용도로 사용되는\s*(?:부분의\s*)?)?바닥면적(?:의)?\s*합계가\s*'+A+r'인 (?:것|경우)',t)
    if m and not re.search(r'층수|지하층|무창층|수용인원|무대부',m.group(1)) and not subs: return [{'t':'area','u':split_uses(m.group(1),known),'a':num(m.group(2)),'alt':bool(re.search(r'이거나|수용인원',t[m.end():])),'b':1}]
    if re.search(r'특수가연물|수용인원|무대부|길이가|근로자|계약전류|행정안전부령|행정자치부령|화재안전기준에서 정하는|격납고|주차|차고|탱크|전기실|발전실|보일러실|연결통로|랙식|다중이용업|공동구|방사성|리튬|층의 바닥면적|층으로서 바닥면적|바닥면적이|무창층',t):
        m=re.match(r'^(.+?)(?:로서|으로서)',t)
        if m and len(m.group(1))<150 and not re.search(r'층수|지하층|바닥면적',m.group(1)) and not re.search(r'주차|차고|전기실|보일러실|무창층',t[:m.end()]):
            alt=re.search(r'연면적\s*'+A,t)
            if alt and not re.search(r'수용인원|무대부|특수가연물',t[:alt.start()]):
                return [{'t':'area','u':split_uses(m.group(1),known),'a':num(alt.group(1)),'alt':True}]
            tt=re.sub(r'\((?:[^()]|\([^()]*\))*\)','',t)
            alt=re.search(r'^(.+?)(?:로서|으로서)\s*(?:해당 용도로 사용되는\s*(?:부분의\s*)?)?바닥면적(?:의)?\s*합계가\s*'+A+r'\s*(?=이거나)',tt)
            if alt and not re.search(r'수용인원|무대부|특수가연물|층의|층으로서|지하층|무창층',alt.group(1)):
                return [{'t':'area','u':split_uses(m.group(1),known),'a':num(alt.group(2)),'alt':True,'b':1}]
            return [{'t':'unknown','u':split_uses(m.group(1),known)}]
        m=re.match(r'^연면적\s*'+A,t)
        if m: return [{'t':'area','u':None,'a':num(m.group(1)),'alt':True}]
        return [{'t':'unknown','u':None}]
    m=re.match(r'^(.+?)(?:로서|으로서)\s*(?:해당 용도로 사용되는\s*)?바닥면적(?:의)?\s*합계가\s*'+A+r'인 것$',t)
    if m and not re.search(r'층수|지하층|무창층',m.group(1)): return [{'t':'area','u':split_uses(m.group(1),known),'a':num(m.group(2)),'alt':False,'b':1}]
    m=re.match(r'^(.+?)(?:로서|으로서)\s*연면적\s*'+A,t)
    if m:
        u=split_uses(m.group(1),known); g=None
        gg=[re.search(r'층수가\s*(\d+)층',x) for x in u]
        if any(gg): g=int([x for x in gg if x][0].group(1)); u=[x for x,y in zip(u,gg) if not y] or None
        return [{'t':'area','u':u,'a':num(m.group(2)),'g':g,'alt':bool(re.search(r'이거나|바닥면적|수용인원',t[m.end():]))}]
    m=re.match(r'^연면적\s*'+A,t)
    if m: return [{'t':'area','u':None,'a':num(m.group(1)),'alt':bool(re.search(r'이거나|바닥면적|수용인원',t[m.end():]))}]
    m=re.match(r'^(?:(.+?)(?:로서|으로서)\s*)?층수가\s*(\d+)층\s*이상(?:으로서|이고)\s*연면적\s*'+A,t)
    if m: return [{'t':'area','u':split_uses(m.group(1),known) if m.group(1) else None,'a':num(m.group(3)),'g':int(m.group(2)),'alt':False}]
    m=re.match(r'^(?:지하층을 포함하는\s*)?층수가\s*(\d+)층\s*이상인\s*(?:것|특정소방대상물|건축물)?(.*)$',t)
    if m:
        rest=m.group(2)
        if re.match(r'^\s*(?:의 경우에는|은|는)?\s*(?:모든 층|전층|\d+층 이상의 층)?\s*[.。]?\s*(?:다만.*)?$',rest):
            return [{'t':'ground','u':None,'g':int(m.group(1)),'x':[]}]
        mm=re.search(r'중\s*(.+?)\s*(?:의\s*)?용도',rest) or re.match(r'^\s*(.+?)(?:의|로서)?\s*(?:모든 층|경우|용도)',rest)
        uu=split_uses(mm.group(1),known) if mm else None
        return [{'t':'unknown','u':['~'+x.lstrip('~') for x in uu] if uu and re.search(r'중\s',rest) else uu}]
    m=re.match(r'^(.+?)(?:로서|으로서)?\s*층수가\s*(\d+)층\s*이상인',t)
    if m and len(m.group(1))<80 and '지하층을 포함' not in m.group(1) and not re.search(r'이상인 (?:것|층) 중',t):
        return [{'t':'ground','u':split_uses(m.group(1),known),'g':int(m.group(2)),'x':[]}]
    m=re.search(r'지하층의 층수가\s*(\d+)\s*이상',t)
    if m: return [{'t':'bcount','c':int(m.group(1))}]
    m=re.match(r'^(.+?)(?:로서|으로서)',t)
    if m and len(m.group(1))<150: return [{'t':'unknown','u':split_uses(m.group(1),known)}]
    if not re.search(r'\d|층|면적|경우|설치',t) and (len(t)<60 or ',' in t): return [{'t':'uses','u':split_uses(re.sub(r'[.:]\s*$','',t),known)}]
    return [{'t':'unknown','u':None}]
EXKEY=['지하구','가스시설','축사','터널','지정문화재']
def facility_specs(h,items,ref,known,subref):
    m=re.match(r'^(?:화재안전기준에 따라\s*)?(.+?)(?:를|을)\s*설치(?:하여|해)야 (?:하는|할)',h) or re.match(r'^(?:화재안전기준에 따라\s*)?(.+?)(?:는|은)\s',h)
    if not m: return None
    name=re.sub(r'\s+',' ',m.group(1)).strip().replace('?','·')
    if len(name)>30 or '(' in name or not re.search(r'(?:설비|기구|등|경보기|감지기|시설|장치|유도표지|방화벽)$',name): return None
    exm=re.search(r'\(([^()]*(?:\([^()]*\)[^()]*)*?)(?:은|는|를|을)\s*제외한다\)',h)
    x=[k for k in EXKEY if exm and re.search(k,exm.group(1)) and not re.search(r'중\s*'+k,exm.group(1))]
    specs=[]
    cond=re.search(r'\(([^()]*?)경우\s*(?:만|에\s*한하여)?\s*해당한다\)|\(([^()]*?)경우에\s*한한다\)',h)
    dep='자동화재탐지설비' if re.search(r'자동화재탐지설비를 설치(?:하여|해)야 하는 특정소방대상물 중',h) else None
    if not items:
        if re.search(r'모든 특정소방대상물|모든 소방대상물|별표\s*2의\s*특정\s*소방대상물',h): specs.append({'t':'all','r':ref})
        elif '피난층' in h and '모든 층' in h and '피난기구' in name: specs.append({'t':'refuge','r':ref})
        else:
            mm=re.search(r'(?:은|는)\s*(.+?)(?:로|으로) 한다',h)
            if mm and not re.search(r'\d|층|면적',mm.group(1)): specs.append({'t':'uses','u':split_uses(mm.group(1),known),'r':ref})
            else: specs.append({'t':'unknown','u':None,'r':ref})
    for it in items:
        txt=it['t'];r=ref+subref(it['k'])
        if '피난층' in txt and '모든 층' in txt and '피난기구' in name: specs.append({'t':'refuge','r':r});continue
        mm=re.search(r'설치(?:하여|해)야 하는 것\s*[:：]\s*(.+)$',txt)
        if mm and not it['c']: specs.append({'t':'uses','u':split_uses(mm.group(1),known),'r':r});continue
        res=classify(txt,known,it['c'])
        if it['c'] and res==[{'t':'unknown','u':None}]:
            res=[c for sub in it['c'] for c in classify(sub['t'],known)]
        for c in res: c['r']=r;specs.append(c)
    fa={'n':name,'x':x,'s':specs}
    if cond: fa['c']=re.sub(r'\s+',' ',(cond.group(1) or cond.group(2) or '').strip()+' 경우 확인')
    if dep: fa['d']=dep
    return fa
def facilities(root,annex,known):
    facs=[]
    digits=[c for c in root['c'] if c['k'].isdigit()]
    facility_first=len(digits)>=8 and sum(1 for c in digits if re.search(r'설치(?:하여|해)야|설치한다|설치(?:하여|해)야 한다',c['t']))>=len(digits)*0.6
    if root['new'] and not facility_first:
        cat=0;flat=[]
        for node in root['c']:
            if node['k'].isdigit():
                cat=int(node['k'])
                if re.search(r'설치(?:하여|해)야|설치한다',node['t']) and node['c'] and all(not re.search(r'설치(?:하여|해)야|설치한다',c['t'][:40]) for c in node['c']):
                    tt=re.sub(r'^\D*?설비(?=[가-힣]+설비를)','',node['t'])
                    fa=facility_specs(tt,node['c'],'별표 '+annex+' 제'+str(cat)+'호',known,lambda k:k+'목')
                    if fa: facs.append(fa)
                    continue
                for f in node['c']: flat.append((cat,f))
            else:
                if node['k']=='가': cat+=1
                flat.append((cat,node))
        for cat,f in flat:
            fa=facility_specs(f['t'],f['c'],'별표 '+annex+' 제'+str(cat)+'호'+f['k']+'목',known,lambda k:k+')')
            if fa: facs.append(fa)
    else:
        for f in root['c']:
            if not (f['k'].isdigit() or f['k']=='?'): continue
            fa=facility_specs(f['t'],f['c'],'별표 '+annex+(' 제'+f['k']+'호' if f['k']!='?' else ' '+re.sub(r'(?:을|를)\s*설치.*$','',f['t'])[:12]+' '),known,lambda k:k+'목')
            if fa: facs.append(fa)
    return facs

def known_uses(root):
    for b in root.iter('별표단위'):
        if b.findtext('별표번호') == '0002' and (b.findtext('별표가지번호') or '00') in ('00', ''):
            out, n = [], 1
            for ln in clean_lines(b.findtext('별표내용') or ''):
                m = re.match(r'^(\d+)\.\s*(.+)$', ln)
                if m and int(m.group(1)) == n:
                    name = re.split(r'[:(：]', m.group(2))[0]
                    out.append(re.sub(r'\s', '', name)); n += 1
                elif m and int(m.group(1)) == 1 and n > 5:
                    break
            return out
    return []

def parse_decree(path):
    root = ET.parse(path).getroot()
    date = (root.findtext('.//기본정보/시행일자') or root.findtext('.//시행일자') or '').strip()
    name = (root.findtext('.//기본정보/법령명_한글') or '').strip()
    txt = None
    for b in root.iter('별표단위'):
        if (b.findtext('별표가지번호') or '00') in ('00', '') and re.search(r'갖추어야|설치(?:하여|해)야 하는 소방시설|소방시설의 종류', b.findtext('별표제목') or ''):
            txt = b.findtext('별표내용'); annex = str(int(b.findtext('별표번호'))); break
    if not txt:
        return None
    facs = facilities(tree(txt), annex, known_uses(root))
    return {'date': date, 'name': name, 'annex': annex, 'f': facs,
            'sha': hashlib.sha256(txt.encode('utf-8')).hexdigest()}

def canon(f):
    return json.dumps(f, ensure_ascii=False, sort_keys=True)

def diff(base, new):
    a = {f['n']: canon(f) for f in base}; b = {f['n']: canon(f) for f in new}
    changed = sorted(n for n in a if n in b and a[n] != b[n])
    return changed, sorted(n for n in b if n not in a), sorted(n for n in a if n not in b)

def candidates(base_date):
    seen = {}
    for p in sorted(glob.glob(os.path.join(ROOT, 'law_sources_v1', '*', 'installation-decree.xml'))):
        seen['cur'] = p
    out = []
    if 'cur' in seen:
        out.append(('수집 최신본', seen['cur']))
    idx = os.path.join(ROOT, 'law_history_sources_v1', 'index.json')
    if os.path.exists(idx):
        for v in json.load(open(idx, encoding='utf-8')).get('versions', []):
            if v.get('name') == LAW and str(v.get('date', '')) > base_date and v.get('source'):
                out.append(('공포·예정본 ' + v['date'], os.path.join(ROOT, 'law_history_sources_v1', v['source'])))
    return out

def fmt(d):
    return d[:4] + '.' + d[4:6] + '.' + d[6:]

def main():
    base = json.load(open(os.path.join(OUT, 'baseline.json'), encoding='utf-8'))
    found, held = {}, []
    for label, path in candidates(base['date']):
        if not os.path.exists(path):
            continue
        try:
            p = parse_decree(path)
        except Exception as e:  # 분석 실패는 반영하지 않고 기록만
            held.append({'source': label, 'reason': '원문 분석 실패: ' + type(e).__name__}); continue
        if not p or not re.fullmatch(r'\d{8}', p['date']) or p['date'] <= base['date']:
            continue
        if p['name'] and p['name'] != LAW:
            continue
        if len(p['f']) < max(10, int(len(base['f']) * 0.8)):
            held.append({'source': label, 'date': p['date'], 'reason': '분석된 시설 수 비정상(%d개, 기준본 %d개)' % (len(p['f']), len(base['f']))}); continue
        changed, added, removed = diff(base['f'], p['f'])
        prev = found.get(p['date'])
        if prev and prev['sha'] == p['sha']:
            continue
        found[p['date']] = {**p, 'source': label, 'changed': changed, 'added': added, 'removed': removed}
    rules = []
    for date in sorted(found):
        v = found[date]
        if not (v['changed'] or v['added'] or v['removed']):
            continue
        rules.append({'id': 'kr-fire-%s-auto' % date, 'date': date, 'base': base['engine'], 'baseDate': base['date'],
                      'annex': v['annex'], 'sha': v['sha'][:16], 'source': v['source'],
                      'changed': v['changed'], 'added': v['added'], 'removed': v['removed'],
                      'f': v['f'], 'generatedAt': dt.date.today().isoformat()})
    payload = {'schemaVersion': 1, 'baseDate': base['date'], 'baseF': base['f'], 'rules': rules}
    block = BEGIN + '<script id="law-auto-rules">window.LAW_AUTO_RULES=' + json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/') + ';</script>' + END
    app = open(APP, encoding='utf-8').read()
    if BEGIN not in app or END not in app:
        sys.exit('앱에 LAW-AUTO 블록이 없습니다.')
    new_app = app[:app.index(BEGIN)] + block + app[app.index(END) + len(END):]
    status = {'checkedAt': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
              'baseDate': base['date'],
              'versions': [{'date': d, 'source': v['source'], 'changed': v['changed'], 'added': v['added'], 'removed': v['removed']} for d, v in sorted(found.items())],
              'applied': [r['id'] for r in rules], 'held': held}
    os.makedirs(OUT, exist_ok=True)
    prev_status = None
    sp = os.path.join(OUT, 'status.json')
    if os.path.exists(sp):
        prev_status = json.load(open(sp, encoding='utf-8'))
    json.dump(status, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    changed_app = new_app != app
    if changed_app:
        open(APP, 'w', encoding='utf-8').write(new_app)
    # 알림 본문 — 새로 반영되거나 보류된 경우에만
    newly = [r for r in rules if not prev_status or r['id'] not in prev_status.get('applied', [])]
    new_held = [h for h in held if not prev_status or h not in prev_status.get('held', [])]
    if newly or new_held:
        lines = []
        for r in newly:
            lines.append('### %s 시행 개정 — 자동 반영' % fmt(r['date']))
            lines.append('- 원문: %s (별표 %s)' % (r['source'], r['annex']))
            if r['changed']: lines.append('- 기준 변경 시설: ' + ', '.join(r['changed']))
            if r['added']: lines.append('- 새로 추가된 시설: ' + ', '.join(r['added']))
            if r['removed']: lines.append('- 삭제·통합된 시설: ' + ', '.join(r['removed']))
            lines.append('- 앱은 시행일(%s)부터 바뀐 시설을 새 기준으로 판정합니다.' % fmt(r['date']))
            lines.append('')
        for h in new_held:
            lines.append('### 자동 반영 보류 — ' + h.get('source', ''))
            lines.append('- 사유: ' + h['reason'])
            lines.append('')
        open(os.environ.get('LAW_AUTO_NOTICE') or os.path.join(OUT, 'notice.md'), 'w', encoding='utf-8').write('\n'.join(lines))
        print('NOTICE')
    print(json.dumps({'appChanged': changed_app, 'applied': status['applied'], 'held': len(held)}, ensure_ascii=False))

if __name__ == '__main__':
    main()

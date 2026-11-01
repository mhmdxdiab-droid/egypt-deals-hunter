"""Direct public-store discovery. No deal aggregator, account session, AI or browser bypass."""
import argparse, hashlib, json, os, re, secrets, sqlite3, time, socket
import urllib.request, urllib.error, urllib.parse, urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

BASE='https://mobaco.com'
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'private'
CAIRO=ZoneInfo('Africa/Cairo')
UA='EgyptSmartDealsHunter/0.3 (personal price monitor; public HTML only)'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

class HTTP:
    def __init__(self,delay=20):
        self.delay=max(20,delay);self.last=0;self.robots=None
        self.opener=urllib.request.build_opener(NoRedirect())
    def raw(self,url):
        p=urllib.parse.urlsplit(url)
        if p.scheme!='https' or p.netloc!='mobaco.com' or p.username or p.fragment:raise ValueError('Source outside allowlist')
        time.sleep(max(0,self.delay-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        with self.opener.open(urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xml'}),timeout=25) as r:
            data=r.read(3_000_001)
            if len(data)>3_000_000:raise ValueError('Oversized response')
            text=data.decode('utf-8')
        if any(x in text.lower() for x in ('cf-chl-', 'challenge-platform', 'verify you are human', 'إجراء التحقق من الأمان')):
            raise PermissionError('Human verification required; stopping, no bypass')
        return text
    def get(self,url):
        if self.robots is None:
            raw=self.raw(BASE+'/robots.txt')
            if '<html' in raw.lower() or not re.search(r'(?im)^user-agent:',raw):raise PermissionError('Invalid robots')
            self.robots=urllib.robotparser.RobotFileParser();self.robots.parse(raw.splitlines())
            self.delay=max(self.delay,self.robots.crawl_delay(UA) or 0)
        if not self.robots.can_fetch(UA,url):raise PermissionError('robots disallows URL')
        return self.raw(url)

class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.kind=None;self.data=[];self.scripts=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='script':self.kind='nuxt' if a.get('id')=='__NUXT_DATA__' else 'ld' if a.get('type')=='application/ld+json' else None;self.data=[]
    def handle_data(self,data):
        if self.kind:self.data.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.kind:self.scripts.append((self.kind,''.join(self.data)));self.kind=None

class Decoder:
    """Decode only the data graph embedded in HTML. Never eval or execute page JS."""
    def __init__(self,nodes):
        if not isinstance(nodes,list) or len(nodes)>200000:raise ValueError('Invalid hydration data')
        self.nodes=nodes
    def get(self,index,depth=0,seen=frozenset()):
        if not isinstance(index,int) or isinstance(index,bool):raise ValueError('Invalid reference')
        if index==-1:return None
        if depth>40 or index in seen or not 0<=index<len(self.nodes):raise ValueError('Unsupported/cyclic data')
        n=self.nodes[index];seen=seen|{index}
        dec=lambda i:self.get(i,depth+1,seen)
        if isinstance(n,dict):return {k:dec(v) for k,v in n.items()}
        if isinstance(n,list):
            if n and isinstance(n[0],str):
                if n[0] not in ('Reactive','Ref','ShallowRef','ShallowReactive') or len(n)!=2:raise ValueError('Unsupported tagged data')
                return dec(n[1])
            return [dec(v) for v in n]
        return n

def money(value):
    d=Decimal(str(value))
    if not d.is_finite() or d<=0:raise ValueError('Invalid price')
    return int((d*100).quantize(Decimal('1')))

@dataclass
class Variant:
    sku:str
    parent_sku:str
    title:str
    group:str
    size:str|None
    color:str|None
    price:int
    listed_before:int|None
    stock:float|None
    url:str


def parse_page(body,url):
    parser=Scripts();parser.feed(body)
    products=[];nodes=None
    for kind,data in parser.scripts:
        try:j=json.loads(data)
        except ValueError:continue
        if kind=='nuxt':nodes=j
        if kind=='ld':
            candidates=j if isinstance(j,list) else [j]
            for obj in candidates:
                if obj.get('@type')=='Product':products.append(obj)
    if not products:return []
    if len(products)!=1 or not nodes:raise ValueError('Ambiguous/missing product payload')
    product=products[0];offer=product.get('offers',{})
    if not isinstance(offer,dict) or offer.get('priceCurrency')!='EGP':raise ValueError('EGP not proven')
    d=Decoder(nodes)
    parents=[n for n in nodes if isinstance(n,dict) and 'variants' in n and 'sku' in n and d.get(n['sku'])==product.get('sku')]
    if len(parents)!=1:raise ValueError('Main product identity not proven')
    parent=parents[0];sku=d.get(parent['sku']);title=d.get(parent['name']);group=d.get(parent['mainCategory']) if 'mainCategory' in parent else 'unknown'
    variants=d.get(parent['variants']);result=[]
    if not isinstance(variants,list):raise ValueError('Variant list missing')
    for v in variants:
        if v.get('parentSku')!=sku or not v.get('sku'):raise ValueError('Variant parent mismatch')
        attributes={a['code']:a.get('label') for a in v.get('attributes',[])}
        stock=v.get('stock')
        if stock is not None and (not isinstance(stock,(int,float)) or isinstance(stock,bool) or stock<0):raise ValueError('Stock ambiguous')
        result.append(Variant(v['sku'],sku,title.strip(),group,attributes.get('size'),attributes.get('color'),money(v['price']),money(v['priceBefore']) if v.get('priceBefore') else None,stock,url))
    if len({v.sku for v in result})!=len(result):raise ValueError('Duplicate SKU')
    return result


def sitemap_urls(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():raise ValueError('Unsafe XML')
    root=ET.fromstring(xml)
    if root.tag.split('}')[-1]!='urlset':raise ValueError('Unsupported sitemap kind')
    products=[];categories=[]
    for e in root.iter():
        if e.tag.split('}')[-1]!='loc' or not e.text:continue
        u=e.text;p=urllib.parse.urlsplit(u)
        if p.scheme!='https' or p.netloc!='mobaco.com' or not p.path.startswith('/en/') or p.query:continue
        parts=p.path.strip('/').split('/')
        if len(parts)==2 and parts[1] not in ('men','women','boys','girls','accessories',''):products.append(u)
        else:categories.append(u)
    return sorted(set(products)),sorted(set(categories))


class Store:
    def __init__(self,path):
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY,v TEXT);
        CREATE TABLE IF NOT EXISTS categories(url TEXT PRIMARY KEY,last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,next_check REAL NOT NULL DEFAULT 0,last_ok REAL,error TEXT,failures INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS variants(sku TEXT PRIMARY KEY,parent_sku TEXT,title TEXT,group_name TEXT,size TEXT,color TEXT,url TEXT REFERENCES pages(url),last_seen REAL,stock REAL,current_price INTEGER,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,sku TEXT NOT NULL REFERENCES variants(sku),at REAL NOT NULL,price INTEGER NOT NULL CHECK(price>0),stock REAL,listed_before INTEGER);
        CREATE INDEX IF NOT EXISTS ix_history ON observations(sku,at);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,sku TEXT,parent_sku TEXT,price INTEGER,at REAL,status TEXT);
        ''')
    def discover(self,http,force=False):
        row=self.db.execute("SELECT v FROM state WHERE k='last_discovery'").fetchone()
        if not force and row and time.time()-float(row[0])<86400:return
        products,categories=sitemap_urls(http.get(BASE+'/en-sitemap.xml'))
        if not products:raise ValueError('Empty sitemap; preserving existing coverage')
        now=time.time()
        with self.db:
            self.db.execute('UPDATE pages SET active=0')
            for u in products:
                self.db.execute('INSERT OR IGNORE INTO pages(url) VALUES(?)',(u,));self.db.execute('UPDATE pages SET active=1 WHERE url=?',(u,))
            for u in categories:self.db.execute('INSERT OR REPLACE INTO categories VALUES(?,?)',(u,now))
            self.db.execute("INSERT OR REPLACE INTO state VALUES('last_discovery',?)",(str(now),))
    def ingest(self,url,variants,at=None):
        now=time.time() if at is None else at
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO pages(url) VALUES(?)',(url,))
            self.db.execute('UPDATE variants SET active=0 WHERE url=?',(url,))
            for v in variants:
                old=self.db.execute('SELECT * FROM variants WHERE sku=?',(v.sku,)).fetchone()
                if old and any(old[k]!=getattr(v,k) for k in ('parent_sku','size','color')):raise ValueError('SKU identity changed')
                self.db.execute('''INSERT INTO variants(sku,parent_sku,title,group_name,size,color,url,last_seen,stock,current_price,active) VALUES(?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(sku) DO UPDATE SET title=excluded.title,group_name=excluded.group_name,url=excluded.url,last_seen=excluded.last_seen,stock=excluded.stock,current_price=excluded.current_price,active=1''',
                  (v.sku,v.parent_sku,v.title,v.group,v.size,v.color,url,now,v.stock,v.price))
                last=self.db.execute('SELECT * FROM observations WHERE sku=? ORDER BY at DESC LIMIT 1',(v.sku,)).fetchone()
                if not last or last['price']!=v.price or last['stock']!=v.stock or last['listed_before']!=v.listed_before or now-last['at']>=86400:
                    self.db.execute('INSERT INTO observations(sku,at,price,stock,listed_before) VALUES(?,?,?,?,?)',(v.sku,now,v.price,v.stock,v.listed_before))
            self.db.execute('UPDATE pages SET last_ok=?,next_check=?,error=NULL,failures=0 WHERE url=?',(now,now+21600,url))
    def candidates(self,config,at=None):
        now=time.time() if at is None else at;out=[];parents=set()
        for v in self.db.execute('SELECT v.* FROM variants v JOIN pages p ON p.url=v.url WHERE v.active=1 AND p.active=1 AND v.stock>0 AND v.last_seen>?',(now-900,)):
            if v['parent_sku'] in parents or config.get('size') and v['size']!=config['size']:continue
            if config.get('max_price_egp') and v['current_price']>config['max_price_egp']*100:continue
            if config.get('keywords') and not any(w.lower() in v['title'].lower() for w in config['keywords']):continue
            prior=list(self.db.execute('SELECT * FROM observations WHERE sku=? AND at<? AND at>=? AND stock>0 ORDER BY at',(v['sku'],v['last_seen']-60,now-90*86400)))
            # Daily medians avoid overweighting frequent polls. Stock-unknown observations never establish buying history.
            days={}
            for o in prior:days.setdefault(datetime.fromtimestamp(o['at'],CAIRO).date(),[]).append(o['price'])
            if len(days)<7 or not prior or now-prior[0]['at']<7*86400:continue
            baseline=median([median(prices) for prices in days.values()])
            saving=(1-v['current_price']/baseline)*100
            if saving<config.get('min_drop_percent',25):continue
            last=self.db.execute("SELECT * FROM notifications WHERE parent_sku=? AND status IN ('sending','sent','uncertain') ORDER BY at DESC LIMIT 1",(v['parent_sku'],)).fetchone()
            if last and (now-last['at']<21600 or v['current_price']>last['price']*.95):continue
            out.append((dict(v),round(saving,1),baseline,len(days)));parents.add(v['parent_sku'])
        return out
    def stats(self):
        scalar=lambda q:self.db.execute(q).fetchone()[0]
        return {'source':'Mobaco public HTML','discovered_candidate_pages':scalar('SELECT COUNT(*) FROM pages WHERE active=1'),
          'category_urls':scalar('SELECT COUNT(*) FROM categories'),'product_pages_verified':scalar('SELECT COUNT(DISTINCT url) FROM variants'),
          'variant_skus':scalar('SELECT COUNT(*) FROM variants'),'last_observation_in_stock':scalar('SELECT COUNT(*) FROM variants WHERE active=1 AND stock>0'),
          'unknown_stock':scalar('SELECT COUNT(*) FROM variants WHERE active=1 AND stock IS NULL'),'observations':scalar('SELECT COUNT(*) FROM observations'),
          'failed_pages':scalar('SELECT COUNT(*) FROM pages WHERE error IS NOT NULL')}


def bot(token,method,body):
    if not re.fullmatch(r'\d+:[\w-]+',token):raise ValueError('Invalid token')
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=35) as r:result=json.loads(r.read(1000000))
        if not result.get('ok'):raise ValueError()
        return result['result']
    except Exception:raise RuntimeError('Telegram request failed; sensitive details suppressed') from None


def alert(store,http,config):
    token=config.get('bot_token');chat=config.get('chat_id')
    if not token or not chat:return
    now=time.time();hour=datetime.now(CAIRO).hour
    if hour>=23 or hour<8:return
    sent=list(store.db.execute("SELECT at FROM notifications WHERE status IN ('sent','sending','uncertain')"))
    today=datetime.now(CAIRO).date();remaining=max(0,config.get('daily_limit',5)-sum(datetime.fromtimestamp(r[0],CAIRO).date()==today for r in sent))
    for row,saving,baseline,days in store.candidates(config)[:remaining]:
        # Independent second live fetch of the same page and exact variant before an alert.
        verified=parse_page(http.get(row['url']),row['url'])
        match=next((v for v in verified if v.sku==row['sku'] and v.price==row['current_price'] and v.stock is not None and v.stock>0),None)
        store.ingest(row['url'],verified)
        if not match:continue
        with store.db:
            nid=store.db.execute("INSERT INTO notifications(sku,parent_sku,price,at,status) VALUES(?,?,?,?,'sending')",(match.sku,match.parent_sku,match.price,time.time())).lastrowid
        message=(f'📉 انخفاض مرصود مباشرة — Mobaco\n{match.title}\nالسعر: {match.price/100:.2f} جنيه\n'
          f'المقاس: {match.size or "غير محدد"} | كود اللون: {match.color or "غير محدد"}\nSKU: {match.sku}\n'
          f'أقل من وسيط {days} أيام رصد بنسبة {saving}%؛ الوسيط {baseline/100:.2f} جنيه.\n'
          'الشحن غير معروف؛ المقارنة لسعر السلعة فقط. المخزون بحسب صفحة المتجر، وليس ضمان إتمام الشراء.\n'
          f'آخر تحقق: {datetime.now(CAIRO):%Y-%m-%d %H:%M %Z}\nاختر النسخة المذكورة من صفحة المنتج:\n{match.url}')
        try:bot(token,'sendMessage',{'chat_id':chat,'text':message});status='sent'
        except Exception:status='uncertain'
        with store.db:store.db.execute('UPDATE notifications SET status=? WHERE id=?',(status,nid))


def once(store,http,config,limit=2):
    store.discover(http)
    urls=[r[0] for r in store.db.execute('SELECT url FROM pages WHERE active=1 AND next_check<=? ORDER BY last_ok IS NOT NULL,next_check,url LIMIT ?',(time.time(),limit))]
    for url in urls:
        try:store.ingest(url,parse_page(http.get(url),url))
        except Exception as exc:
            with store.db:
                store.db.execute('UPDATE pages SET failures=failures+1,error=?,next_check=? WHERE url=?',(type(exc).__name__,time.time()+3600,url))
            if isinstance(exc,(PermissionError,urllib.error.HTTPError)):
                # Stop this source batch on protection, authorization or rate-limit responses.
                raise
    alert(store,http,config)
    print(json.dumps(store.stats(),ensure_ascii=False,indent=2))


def setup():
    import getpass
    config={'keywords':[],'size':None,'max_price_egp':None,'min_drop_percent':25,'daily_limit':5}
    config['keywords']=[w.strip() for w in input('كلمات عنوان المنتج بالإنجليزية، مفصولة بفاصلة؛ Enter لكل المنتجات: ').split(',') if w.strip()]
    config['size']=input('المقاس كما يكتبه المتجر، أو Enter للكل: ').strip() or None
    cap=input('أقصى سعر بالجنيه، أو Enter: ').strip();config['max_price_egp']=float(cap) if cap else None
    token=getpass.getpass('Bot Token لبوتك أنت، أو Enter للرصد فقط: ').strip()
    if token:
        me=bot(token,'getMe',{});nonce=secrets.token_hex(8)
        print('افتح الرابط من حسابك واضغط Start: https://t.me/'+me['username']+'?start='+nonce)
        input('بعد Start اضغط Enter هنا: ')
        updates=bot(token,'getUpdates',{'timeout':20})
        messages=[u['message'] for u in updates if u.get('message',{}).get('text')=='/start '+nonce and u['message']['chat'].get('type')=='private']
        if len(messages)!=1:raise ValueError('Pairing not found; run setup again')
        config.update(bot_token=token,chat_id=messages[0]['chat']['id'])
    path=DATA/'settings.json'
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2));os.chmod(path,0o600)
    print('الإعداد محفوظ محليًا. لا حاجة إلى API Hash أو جلسة حسابك أو قنوات عروض.')


def main():
    os.umask(0o077);DATA.mkdir(exist_ok=True)
    p=argparse.ArgumentParser();p.add_argument('command',choices=['setup','once','run','stats']);p.add_argument('--limit',type=int,default=2);a=p.parse_args()
    if not 1<=a.limit<=20:raise SystemExit('limit must be 1..20')
    if a.command=='setup':setup();return
    store=Store(DATA/'catalog.sqlite3')
    if a.command=='stats':print(json.dumps(store.stats(),indent=2));return
    guard=socket.socket()
    try:guard.bind(('127.0.0.1',47393))
    except OSError:raise SystemExit('Another direct-monitor instance is already running')
    http=HTTP()
    while True:
        config=json.loads((DATA/'settings.json').read_text()) if (DATA/'settings.json').exists() else {}
        try:once(store,http,config,a.limit)
        except Exception as exc:
            print('Source paused:',type(exc).__name__)
            if a.command=='once':raise SystemExit(1)
            time.sleep(3600)
        if a.command=='once':return
        time.sleep(60)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Stopped; observations saved.')

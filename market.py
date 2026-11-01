"""Direct 2B public catalog monitoring. No AI, internal APIs or CAPTCHA bypass."""
import json, os, time, re, urllib.request, urllib.parse, urllib.robotparser
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from direct import Store, Scripts, bot, CAIRO
from datetime import datetime
BASE='https://2b.com.eg'
SEED=BASE+'/ar/mobile-and-tablet/mobiles.html'
UA='EgyptSmartDealsHunter/0.6 (personal public catalog monitor)'

class Links(HTMLParser):
 def __init__(self):super().__init__();self.urls=[]
 def handle_starttag(self,t,a):
  d=dict(a)
  if t=='a' and d.get('href'):self.urls.append(d['href'])

def safe(u):
 p=urllib.parse.urlsplit(u)
 return p.scheme=='https' and p.netloc=='2b.com.eg' and not p.query and not p.fragment and p.path.startswith('/ar/') and p.path.endswith('.html')

def products(html):
 p=Scripts();p.feed(html);out={}
 def walk(v):
  if isinstance(v,list):
   for x in v:walk(x)
  elif isinstance(v,dict):
   if v.get('@type')=='Product':
    offers=v.get('offers',[]);offers=[offers] if isinstance(offers,dict) else offers
    if len(offers)!=1:return
    o=offers[0];u=v.get('url') or o.get('url','')
    if not safe(u) or o.get('priceCurrency')!='EGP' or not v.get('name'):return
    try:
     n=Decimal(str(o['price']))*100
     if not n.is_finite() or n<=0 or n!=n.to_integral_value():return
    except (KeyError,InvalidOperation):return
    out[u]={'url':u,'title':v['name'],'price':int(n),'stock':True if o.get('availability','').endswith('/InStock') else False if o.get('availability','').endswith('/OutOfStock') else None}
   elif '@graph' in v:walk(v['@graph'])
 for k,t in p.scripts:
  if k=='ld':
   try:walk(json.loads(t))
   except (ValueError,TypeError):continue
 return list(out.values())

class Source:
 def __init__(self):self.last=0;self.rules=None
 def raw(self,u):
  if urllib.parse.urlsplit(u).netloc!='2b.com.eg':raise ValueError('Outside source')
  time.sleep(max(0,20-(time.monotonic()-self.last)));self.last=time.monotonic()
  with urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':UA}),timeout=30) as r:
   if urllib.parse.urlsplit(r.url).netloc!='2b.com.eg':raise ValueError('Unexpected redirect')
   b=r.read(4_000_001)
   if len(b)>4_000_000:raise ValueError('Oversized')
   s=b.decode()
  if any(x in s.lower() for x in ['cf-chl-','challenge-platform','verify you are human']):raise PermissionError('Verification required; source stopped')
  return s
 def get(self,u):
  if self.rules is None:
   s=self.raw(BASE+'/robots.txt')
   if 'user-agent:' not in s.lower():raise PermissionError('Invalid robots')
   self.rules=urllib.robotparser.RobotFileParser();self.rules.parse(s.splitlines())
  if not self.rules.can_fetch(UA,u):raise PermissionError('robots disallows page')
  return self.raw(u)

def run():
 Path('private').mkdir(exist_ok=True)
 s=Store('private/2b.sqlite3');db=s.db;src=Source()
 db.executescript('''CREATE TABLE IF NOT EXISTS catalog_pages(url TEXT PRIMARY KEY,last_ok REAL NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS market_products(url TEXT PRIMARY KEY,title TEXT NOT NULL,price INTEGER NOT NULL,stock INTEGER,last_seen REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS market_history(id INTEGER PRIMARY KEY,url TEXT NOT NULL,price INTEGER NOT NULL,stock INTEGER,at REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS market_alerts(id INTEGER PRIMARY KEY,url TEXT NOT NULL,price INTEGER NOT NULL,at REAL NOT NULL,status TEXT NOT NULL);''')
 token=os.environ.get('TELEGRAM_BOT_TOKEN');chat=os.environ.get('TELEGRAM_CHAT_ID');notify=bool(token and chat and chat.isdigit())
 with db:db.execute('INSERT OR IGNORE INTO catalog_pages(url) VALUES(?)',(SEED,))
 extra=[r[0] for r in db.execute('SELECT url FROM catalog_pages WHERE url!=? ORDER BY last_ok,url LIMIT 2',(SEED,))]
 summary=['# أسعار 2B — رصد مباشر', 'وقت الفحص: '+datetime.now(CAIRO).isoformat(), '| المنتج | السعر EGP | المخزون |','|---|---:|---|'];seen=set();sent=0;fetched=0
 for page in [SEED]+extra:
  html=src.get(page);items=products(html);fetched+=1
  # Discover category pages from public navigation links, never user-entered products.
  links=Links();links.feed(html)
  with db:
   for href in links.urls:
    u=urllib.parse.urljoin(BASE,href)
    if safe(u) and urllib.parse.urlsplit(u).path.count('/')>=3:db.execute('INSERT OR IGNORE INTO catalog_pages(url) VALUES(?)',(u,))
   db.execute('UPDATE catalog_pages SET last_ok=? WHERE url=?',(time.time(),page))
  for v in items:
   if v['url'] in seen:continue
   seen.add(v['url']);old=db.execute('SELECT * FROM market_products WHERE url=?',(v['url'],)).fetchone();now=time.time()
   if old and old['title']!=v['title']:print('Identity changed; skipping:',v['url']);continue
   with db:
    if not old or old['price']!=v['price'] or old['stock']!=v['stock'] or now-old['last_seen']>=86400:db.execute('INSERT INTO market_history(url,price,stock,at) VALUES(?,?,?,?)',(v['url'],v['price'],v['stock'],now))
    db.execute('INSERT OR REPLACE INTO market_products VALUES(?,?,?,?,?)',(v['url'],v['title'],v['price'],v['stock'],now))
   summary.append(f"| [{v['title'].replace('|','/')}]({v['url']}) | {v['price']/100:.2f} | {'متاح بحسب المصدر' if v['stock'] else 'غير متاح/غير معروف'} |")
   if old and v['price']<old['price'] and v['stock'] and notify and sent<5:
    last=db.execute("SELECT * FROM market_alerts WHERE url=? AND status IN ('sending','sent','uncertain') ORDER BY at DESC LIMIT 1",(v['url'],)).fetchone()
    if last and (now-last['at']<21600 or v['price']>=last['price']):continue
    verified=products(src.get(v['url']))
    if not any(x['url']==v['url'] and x['price']==v['price'] and x['stock'] for x in verified):continue
    msg=f"📉 السعر نزل — 2B\n{v['title']}\nكان: {old['price']/100:.2f} جنيه\nبقى: {v['price']/100:.2f} جنيه\nفرق: {(old['price']-v['price'])/100:.2f} جنيه\nآخر تحقق: {datetime.now(CAIRO):%Y-%m-%d %H:%M}\nمقارنة بآخر رصد لنفس رابط النسخة. الشحن غير معروف؛ راجع السعة واللون والضمان عند الشراء.\n{v['url']}"
    with db:nid=db.execute("INSERT INTO market_alerts(url,price,at,status) VALUES(?,?,?,'sending')",(v['url'],v['price'],now)).lastrowid
    try:bot(token,'sendMessage',{'chat_id':chat,'text':msg});status='sent'
    except Exception:status='uncertain'
    with db:db.execute('UPDATE market_alerts SET status=? WHERE id=?',(status,nid))
    sent+=1;print('Alert status:',status)
 print(json.dumps({'source':'2B direct public JSON-LD','catalog_pages_fetched':fetched,'products_checked_now':len(seen),'products_discovered_total':db.execute('SELECT COUNT(*) FROM market_products').fetchone()[0],'category_pages_discovered':db.execute('SELECT COUNT(*) FROM catalog_pages').fetchone()[0],'telegram_configured':notify,'alerts_attempted':sent}))
 if not seen:raise RuntimeError('No valid products extracted; no successful coverage claim')
 if os.environ.get('GITHUB_STEP_SUMMARY'):
  with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('\n'.join(summary))
 if notify and not db.execute("SELECT 1 FROM state WHERE k='2b_activation'").fetchone():
  with db:db.execute("INSERT INTO state VALUES('2b_activation','sending')")
  try:bot(token,'sendMessage',{'chat_id':chat,'text':f'✅ اتوقفت جدولة Mobaco. المصدر الجديد المباشر: 2B.\nالدورة دي فحصت {len(seen)} منتجًا وحفظت أسعار البداية. دي رسالة تشغيل وليست عرضًا.\nالمتابعة كل15 دقيقة مستهدفة، مع تدوير الأقسام؛ مش كل منتج يتفحص كل15 دقيقة. المصدر الآخر والمتاجر الإضافية لم تُفعّل بعد.'});status='sent'
  except Exception:status='uncertain'
  with db:db.execute("UPDATE state SET v=? WHERE k='2b_activation'",(status,))
  print('Activation message:',status)

if __name__=='__main__':run()

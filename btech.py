"""Bounded B.TECH catalog monitoring; public product JSON-LD only."""
import os,time,json,urllib.parse
from pathlib import Path
from datetime import datetime
import market as m
from direct import Store,bot,CAIRO
BASE='https://btech.com';SEED=BASE+'/ar/c/mobiles-tablets/mobile-phones'

def safe(u):
 p=urllib.parse.urlsplit(u)
 return p.scheme=='https' and p.netloc=='btech.com' and p.path.startswith('/ar/p/') and not p.query and not p.fragment

def run():
 m.BASE=BASE;m.safe=safe
 src=m.Source();Path('private').mkdir(exist_ok=True);s=Store('private/btech.sqlite3');db=s.db
 db.executescript('''CREATE TABLE IF NOT EXISTS targets(url TEXT PRIMARY KEY,last_ok REAL NOT NULL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS prices(url TEXT PRIMARY KEY,title TEXT NOT NULL,price INTEGER NOT NULL,stock INTEGER,at REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,url TEXT NOT NULL,price INTEGER NOT NULL,stock INTEGER,at REAL NOT NULL);
 CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,url TEXT,price INTEGER,at REAL,status TEXT);''')
 html=src.get(SEED);links=m.Links();links.feed(html)
 with db:
  for href in links.urls:
   u=urllib.parse.urljoin(BASE,href);p=urllib.parse.urlsplit(u)
   # Product canonical URL intentionally uses the default displayed offer, not a seller-specific query.
   u=urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,'',''))
   if safe(u):db.execute('INSERT OR IGNORE INTO targets(url) VALUES(?)',(u,))
 urls=[r[0] for r in db.execute('SELECT url FROM targets ORDER BY last_ok,url LIMIT 6')]
 token=os.environ.get('TELEGRAM_BOT_TOKEN');chat=os.environ.get('TELEGRAM_CHAT_ID');notify=bool(token and chat and chat.isdigit())
 summary=['# أسعار B.TECH — رصد مباشر', 'وقت الفحص: '+datetime.now(CAIRO).isoformat(), '| المنتج | السعر EGP | التوفر |','|---|---:|---|'];checked=0;sent=0
 for u in urls:
  items=m.products(src.get(u));v=next((x for x in items if x['url']==u),None)
  if not v:print('No confirmed EGP product:',u);continue
  old=db.execute('SELECT * FROM prices WHERE url=?',(u,)).fetchone();now=time.time()
  if old and old['title']!=v['title']:print('Identity changed:',u);continue
  with db:
   if not old or old['price']!=v['price'] or old['stock']!=v['stock']:db.execute('INSERT INTO history(url,price,stock,at) VALUES(?,?,?,?)',(u,v['price'],v['stock'],now))
   db.execute('INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)',(u,v['title'],v['price'],v['stock'],now));db.execute('UPDATE targets SET last_ok=? WHERE url=?',(now,u))
  checked+=1
  summary.append(f"| [{v['title'].replace('|','/')}]({u}) | {v['price']/100:.2f} | {v['stock']} |")
  print(json.dumps(v,ensure_ascii=False))
  if old and old['price']>v['price'] and v['stock'] and notify and sent<5:
   last=db.execute("SELECT * FROM alerts WHERE url=? AND status IN ('sent','sending','uncertain') ORDER BY at DESC LIMIT 1",(u,)).fetchone()
   if last and (now-last['at']<21600 or v['price']>=last['price']):continue
   verify=next((x for x in m.products(src.get(u)) if x['url']==u),None)
   if not verify or verify['price']!=v['price'] or not verify['stock']:continue
   text=f"📉 السعر المعروض نزل — B.TECH\n{v['title']}\nكان: {old['price']/100:.2f} جنيه\nبقى: {v['price']/100:.2f} جنيه\nآخر تحقق: {datetime.now(CAIRO):%Y-%m-%d %H:%M}\nرصد صفحة المنتج والعرض الافتراضي؛ البائع والضمان والشحن غير مؤكدين وقد يختلف العرض. ليست مقارنة مؤكدة لنفس البائع ولا حكمًا على خصم تاريخي.\n{u}"
   with db:nid=db.execute("INSERT INTO alerts(url,price,at,status) VALUES(?,?,?,'sending')",(u,v['price'],now)).lastrowid
   try:bot(token,'sendMessage',{'chat_id':chat,'text':text});status='sent'
   except Exception:status='uncertain'
   with db:db.execute('UPDATE alerts SET status=? WHERE id=?',(status,nid))
   sent+=1;print('Price alert:',status)
 stats={'source':'B.TECH direct','product_links_discovered':db.execute('SELECT COUNT(*) FROM targets').fetchone()[0],'products_checked_now':checked,'products_checked_total':db.execute('SELECT COUNT(*) FROM prices').fetchone()[0],'telegram_configured':notify};print(json.dumps(stats))
 if not checked:raise RuntimeError('No product prices verified; not a successful source run')
 summary+=['',json.dumps(stats),'المصدر الحالي B.TECH فقط. البائع والضمان والشحن غير مؤكدين؛ سعر العرض الافتراضي بحسب الصفحة. الجدولة كل15دقيقة مستهدفة، لكن المنتجات تتناوب وليست مراقبة لحظية لكلمنتج.']
 if os.environ.get('GITHUB_STEP_SUMMARY'):
  with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('\n'.join(summary))
 if notify and not db.execute("SELECT 1 FROM state WHERE k='btech_activation'").fetchone():
  with db:db.execute("INSERT INTO state VALUES('btech_activation','sending')")
  try:bot(token,'sendMessage',{'chat_id':chat,'text':f'✅ المصدر الجديد: B.TECH — بيانات مباشرة من صفحات المتجر.\nاكتشفت {stats["product_links_discovered"]} رابط منتج وفحصت فعليًا {checked} منتجات في الدورة دي. دي رسالة تشغيل وليست عرضًا.\nMobaco متوقف. 2B طلب تحقق أمني من GitHub فلم نعتمده كمصدر شغال. باقي المتاجر لسه لم تُفعّل.\nالمتابعة كل15 دقيقة مستهدفة لدفعة متناوبة، وليست تنبيهًا لحظيًا لكل السوق.'});status='sent'
  except Exception:status='uncertain'
  with db:db.execute("UPDATE state SET v=? WHERE k='btech_activation'",(status,))
  print('Activation message:',status)

if __name__=='__main__':run()

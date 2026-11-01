"""Bounded two-product cloud pilot; no AI requests. Secrets never enter SQLite.
Connection verification run after recipient setup.
"""
import os, time, json
from direct import Store, HTTP, parse_page, bot, DATA, CAIRO
from datetime import datetime


def drops(previous, current):
    return [v for v in current if v.sku in previous and v.stock is not None and v.stock>0 and v.price<previous[v.sku]]


def probe(store, token, chat):
    if store.db.execute("SELECT 1 FROM state WHERE k='telegram_probe_v1'").fetchone(): return
    # Reserve before network request: never blindly resend an uncertain test.
    with store.db: store.db.execute("INSERT INTO state(k,v) VALUES('telegram_probe_v1','sending')")
    try:
        bot(token,'sendMessage',{'chat_id':chat,'text':'✅ اختبار اتصال صياد اللقط\nدي رسالة اختبار، مش عرض ولا انخفاض سعر.\nالبرنامج شغال على GitHub، ومش محتاج موبايلك يفضل مفتوح ولا بيستخدم ChatGPT أثناء المتابعة.'})
    except Exception:
        with store.db: store.db.execute("UPDATE state SET v='uncertain' WHERE k='telegram_probe_v1'")
        raise RuntimeError('Telegram test failed or delivery uncertain; check secrets and press Start in your bot. No automatic test resend.') from None
    with store.db: store.db.execute("UPDATE state SET v='sent' WHERE k='telegram_probe_v1'")
    print('Telegram connection test: sent')

def select_urls(store):
    # Share capacity between revisiting known pages and discovering new products.
    known=[r[0] for r in store.db.execute("SELECT url FROM pages WHERE active=1 AND last_ok IS NOT NULL AND next_check<=? ORDER BY last_ok,url LIMIT 6",(time.time(),))]
    fresh=[r[0] for r in store.db.execute("SELECT url FROM pages WHERE active=1 AND last_ok IS NULL AND next_check<=? ORDER BY CASE WHEN url LIKE '%jacket%' OR url LIKE '%polo%' OR url LIKE '%sweat%' THEN 0 ELSE 1 END,url LIMIT ?",(time.time(),12-len(known)))]
    urls=known+fresh
    if len(urls)<12:
        more=[r[0] for r in store.db.execute("SELECT url FROM pages WHERE active=1 AND last_ok IS NOT NULL AND next_check<=? ORDER BY last_ok,url",(time.time(),)) if r[0] not in urls]
        urls+=more[:12-len(urls)]
    return urls

def run():
    DATA.mkdir(exist_ok=True)
    s=Store(DATA/'catalog.sqlite3'); h=HTTP()
    token=os.environ.get('TELEGRAM_BOT_TOKEN'); chat=os.environ.get('TELEGRAM_CHAT_ID')
    if token:
        identity=bot(token,'getMe',{})
        print('Telegram token verified for @'+identity['username'])
    else: print('Missing repository secret: TELEGRAM_BOT_TOKEN')
    if not chat or not chat.isdigit(): print('Missing or invalid repository secret: TELEGRAM_CHAT_ID (positive numeric private chat ID)')
    can_notify=bool(token and chat and chat.isdigit())
    print('Telegram: configured' if can_notify else 'Telegram: disabled; collecting real prices only')
    summary=['# نتيجة فحص الأسعار', '', 'آخر فحص: '+datetime.now(CAIRO).isoformat(), '', 'تنبيهات تليجرام: '+('مفعلة' if can_notify else 'غير مربوطة'), '', '| المنتج | المقاس | اللون | السعر EGP | المخزون بحسب الصفحة |', '|---|---|---|---:|---:|']
    if can_notify: probe(s,token,chat)
    s.discover(h)
    urls=select_urls(s)
    print('Batch pages:',len(urls))
    sent_this_run=0
    for url in urls:
        previous={r['sku']:r['current_price'] for r in s.db.execute('SELECT sku,current_price FROM variants WHERE url=? AND active=1',(url,))}
        try:
            current=parse_page(h.get(url),url); s.ingest(url,current)
        except PermissionError: raise
        except Exception as exc:
            import urllib.error
            with s.db:
                s.db.execute("UPDATE pages SET error=?,failures=failures+1,next_check=? WHERE url=?",(type(exc).__name__,time.time()+3600,url))
            if isinstance(exc,urllib.error.HTTPError) and exc.code in (401,403,429):raise
            print('Page failed:',url,type(exc).__name__)
            continue
        candidates=drops(previous,current)
        for v in current:
            clean=lambda x:str(x).replace("|","/").replace("\n"," ")
            summary.append(f"| [{clean(v.title)}]({url}) | {clean(v.size)} | {clean(v.color)} | {v.price/100:.2f} | {v.stock if v.stock is not None else 'غير معروف'} |")
        if candidates and can_notify and sent_this_run<5:
            checked=parse_page(h.get(url),url); s.ingest(url,checked)
            valid={v.sku:v for v in checked}
            # One message per parent product per run, listing the exact variant.
            for v in candidates:
                if v.sku not in valid or valid[v.sku].price!=v.price or not valid[v.sku].stock:continue
                last=s.db.execute("SELECT price FROM notifications WHERE sku=? AND status IN ('sent','sending','uncertain') ORDER BY at DESC LIMIT 1",(v.sku,)).fetchone()
                if last and v.price>=last[0]:continue
                old=previous[v.sku]
                message=(f'📉 السعر نزل — Mobaco\n{v.title}\nكان: {old/100:.2f} جنيه\nبقى: {v.price/100:.2f} جنيه\n'
                    f'نزول: {(old-v.price)/100:.2f} جنيه ({(old-v.price)/old*100:.1f}%)\n'
                    f'المقاس: {v.size} | اللون: {v.color}\nSKU: {v.sku}\n'
                    f'آخر تحقق: {datetime.now(CAIRO):%Y-%m-%d %H:%M}\n'
                    'مقارنة بآخر سعر رصدناه؛ مش حكم إنه أقل سعر تاريخي. الشحن غير معروف. المخزون بحسب صفحة المتجر.\n'+url)
                with s.db:
                    nid=s.db.execute("INSERT INTO notifications(sku,parent_sku,price,at,status) VALUES(?,?,?,?,'sending')",(v.sku,v.parent_sku,v.price,time.time())).lastrowid
                try:bot(token,'sendMessage',{'chat_id':chat,'text':message}); status='sent'
                except Exception: status='uncertain'
                with s.db:s.db.execute('UPDATE notifications SET status=? WHERE id=?',(status,nid))
                print('Notification status:',status)
                sent_this_run+=1
                break
        print(json.dumps({'url':url,'variants':len(current),'price_drops':len(candidates)},ensure_ascii=False))
    stats=s.stats()
    print(json.dumps(stats))
    summary+=['',f"روابط مرشحة: {stats['discovered_candidate_pages']} | صفحات فُحصت فعليًا: {stats['product_pages_verified']} | نسخ: {stats['variant_skus']}", 'دفعة بحد أقصى12 صفحة، كل ساعة؛ ليست مراقبة لحظية لكل المنتجات. إعادة زيارة الصفحة بعد6 ساعات على الأقل وبحسب طول الطابور. التنبيهات لانخفاض السعر فقط، بحد5 رسائل للدورة.']
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f:f.write("\n".join(summary)+"\n")

if __name__=='__main__': run()

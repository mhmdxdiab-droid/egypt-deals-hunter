"""Bounded two-product cloud pilot; no AI requests. Secrets never enter SQLite."""
import os, time, json
from direct import Store, HTTP, parse_page, bot, DATA, CAIRO
from datetime import datetime


def drops(previous, current):
    return [v for v in current if v.sku in previous and v.stock is not None and v.stock>0 and v.price<previous[v.sku]]


def run():
    DATA.mkdir(exist_ok=True)
    s=Store(DATA/'catalog.sqlite3'); h=HTTP()
    token=os.environ.get('TELEGRAM_BOT_TOKEN'); chat=os.environ.get('TELEGRAM_CHAT_ID')
    can_notify=bool(token and chat and chat.isdigit())
    print('Telegram: configured' if can_notify else 'Telegram: disabled; collecting real prices only')
    summary=['# نتيجة فحص الأسعار', '', 'آخر فحص: '+datetime.now(CAIRO).isoformat(), '', 'تنبيهات تليجرام: '+('مفعلة' if can_notify else 'غير مربوطة'), '', '| المنتج | المقاس | اللون | السعر EGP | المخزون بحسب الصفحة |', '|---|---|---|---:|---:|']
    s.discover(h)
    # Persist the two automatically discovered jacket URLs, so subsequent runs revisit them.
    saved=s.db.execute("SELECT v FROM state WHERE k='cloud_pilot_urls'").fetchone()
    if saved: urls=json.loads(saved[0])
    else:
        urls=[r[0] for r in s.db.execute("SELECT url FROM pages WHERE url LIKE '%jacket%' AND active=1 ORDER BY url LIMIT 2")]
        if not urls:raise RuntimeError('No jacket URLs discovered; no manual or fictional substitute')
        with s.db:s.db.execute("INSERT INTO state(k,v) VALUES('cloud_pilot_urls',?)",(json.dumps(urls),))
    for url in urls:
        previous={r['sku']:r['current_price'] for r in s.db.execute('SELECT sku,current_price FROM variants WHERE url=? AND active=1',(url,))}
        current=parse_page(h.get(url),url); s.ingest(url,current)
        candidates=drops(previous,current)
        for v in current:
            clean=lambda x:str(x).replace("|","/").replace("\n"," ")
            summary.append(f"| [{clean(v.title)}]({url}) | {clean(v.size)} | {clean(v.color)} | {v.price/100:.2f} | {v.stock if v.stock is not None else 'غير معروف'} |")
        if candidates and can_notify:
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
                break
        print(json.dumps({'url':url,'variants':len(current),'price_drops':len(candidates)},ensure_ascii=False))
    print(json.dumps(s.stats()))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as f:f.write("\n".join(summary)+"\n")

if __name__=='__main__': run()

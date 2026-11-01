import json,time,unittest,tempfile
from pathlib import Path
from direct import Decoder,parse_page,sitemap_urls,Variant,Store,money

def page(price=1000,stock=2,sku='SIZE-M',currency='EGP',size='M'):
    # Synthetic fixture, explicitly not a merchant's live prices.
    obj={'sku':'PARENT','name':'Test Jacket','mainCategory':'Men','variants':[
       {'sku':sku,'parentSku':'PARENT','name':'Test Jacket','price':price,'stock':stock,'priceBefore':2000,
        'attributes':[{'code':'size','label':size},{'code':'color','label':'Black'}]}]}
    nodes=[]
    def encode(v):
        i=len(nodes);nodes.append(None)
        if isinstance(v,dict):nodes[i]={k:encode(x) for k,x in v.items()}
        elif isinstance(v,list):nodes[i]=[encode(x) for x in v]
        else:nodes[i]=v
        return i
    encode(obj)
    product={'@type':'Product','sku':'PARENT','offers':{'priceCurrency':currency,'price':price}}
    return '<script type="application/ld+json">'+json.dumps(product)+'</script><script id="__NUXT_DATA__" type="application/json">'+json.dumps(nodes)+'</script>'

URL='https://mobaco.com/en/test-jacket'
class ParserTests(unittest.TestCase):
    def test_variants_have_exact_price_size_stock(self):
        v=parse_page(page(),URL)[0]
        self.assertEqual((v.sku,v.price,v.size,v.stock),('SIZE-M',100000,'M',2))
    def test_unknown_stock_not_invented(self):self.assertIsNone(parse_page(page(stock=None),URL)[0].stock)
    def test_egp_required(self):
        with self.assertRaises(ValueError):parse_page(page(currency='USD'),URL)
    def test_bad_prices(self):
        for x in (0,-1,'NaN','Infinity'):
            with self.assertRaises(ValueError):money(x)
    def test_sitemap_isolated_domain_and_categories(self):
        p,c=sitemap_urls('<urlset><url><loc>https://mobaco.com/en/test-jacket</loc></url><url><loc>https://mobaco.com/en/men/jackets</loc></url><url><loc>https://evil.example/x</loc></url></urlset>')
        self.assertEqual(p,[URL]);self.assertEqual(len(c),1)
    def test_cyclic_hydration_rejected(self):
        with self.assertRaises(ValueError):Decoder([{'x':0}]).get(0)
    def test_script_not_evaluated(self):self.assertEqual(parse_page('<script>alert(1)</script>',URL),[])
    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):sitemap_urls('<!DOCTYPE x><urlset/>')

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.sqlite');self.now=time.time()
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def ingest(self,price=1000,at=None,stock=2,size='M'):
        v=parse_page(page(price=price,stock=stock,size=size),URL)
        self.s.ingest(URL,v,at if at is not None else self.now)
    def test_unchanged_poll_not_duplicate_price(self):
        self.ingest();self.ingest(at=self.now+120)
        self.assertEqual(self.s.stats()['observations'],1)
    def test_price_change_appends(self):
        self.ingest();self.ingest(price=900,at=self.now+120)
        self.assertEqual(self.s.stats()['observations'],2)
    def test_first_observation_is_not_deal(self):
        self.ingest(price=500);self.assertEqual(self.s.candidates({},self.now),[])
    def test_seven_day_history_and_real_drop(self):
        for d in range(10,0,-1):self.ingest(at=self.now-d*86400)
        self.ingest(price=600)
        deals=self.s.candidates({},self.now)
        self.assertEqual(len(deals),1);self.assertEqual(deals[0][1],40)
    def test_listed_price_not_history(self):
        for d in range(10,0,-1):self.ingest(at=self.now-d*86400)
        self.ingest(price=1100)
        self.assertEqual(self.s.candidates({},self.now),[])
    def test_unknown_stock_never_alerts(self):
        for d in range(10,0,-1):self.ingest(at=self.now-d*86400)
        self.ingest(price=500,stock=None)
        self.assertEqual(self.s.candidates({},self.now),[])
    def test_variant_identity_change_rolls_back(self):
        self.ingest()
        with self.assertRaises(ValueError):self.ingest(size='XL')
        self.assertEqual(self.s.db.execute('SELECT size FROM variants').fetchone()[0],'M')
    def test_size_filter(self):
        for d in range(10,0,-1):self.ingest(at=self.now-d*86400)
        self.ingest(price=500)
        self.assertEqual(self.s.candidates({'size':'XL'},self.now),[])
    def test_snapshot_keeps_missing_variant_inactive(self):
        self.ingest();self.s.ingest(URL,[])
        self.assertEqual(self.s.db.execute('SELECT active FROM variants').fetchone()[0],0)
    def test_sitemap_discovery_no_manual_product_input(self):
        class HTTP:
            def get(self,url):return '<urlset><url><loc>'+URL+'</loc></url></urlset>'
        self.s.discover(HTTP())
        self.assertEqual(self.s.stats()['discovered_candidate_pages'],1)

if __name__=='__main__':unittest.main()

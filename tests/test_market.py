import unittest,json
from market import products,safe
class MarketTests(unittest.TestCase):
 def html(self,**kw):
  d={'@type':'Product','name':'Phone 128GB Black','url':'https://2b.com.eg/ar/phone-128-black.html','offers':[{'priceCurrency':'EGP','price':1000,'availability':'https://schema.org/InStock'}]};d.update(kw);return '<script type="application/ld+json">'+json.dumps([d])+'</script>'
 def test_real_format(self):self.assertEqual(products(self.html())[0]['price'],100000)
 def test_unknown_stock(self):self.assertIsNone(products(self.html(offers=[{'priceCurrency':'EGP','price':10}]))[0]['stock'])
 def test_foreign_currency(self):self.assertEqual(products(self.html(offers=[{'priceCurrency':'USD','price':10}])),[])
 def test_multiple_offers(self):self.assertEqual(products(self.html(offers=[{},{}])),[])
 def test_invalid_prices(self):
  for x in [0,-1,'NaN','Infinity',0.001]:self.assertEqual(products(self.html(offers=[{'priceCurrency':'EGP','price':x}])),[])
 def test_wrong_domain(self):self.assertEqual(products(self.html(url='https://evil.example/ar/x.html')),[])
 def test_query_excluded(self):self.assertFalse(safe('https://2b.com.eg/ar/x.html?price=0'))

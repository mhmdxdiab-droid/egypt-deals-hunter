import unittest,json
from unittest.mock import patch
import market
from btech import safe
class BtechTests(unittest.TestCase):
 def test_public_product_format(self):
  d={'@type':'Product','name':'Phone 128GB','sku':'ABC','offers':{'price':8499,'priceCurrency':'EGP','availability':'https://schema.org/InStock','url':'https://btech.com/ar/p/abc'}}
  with patch('market.safe',safe):
   v=market.products('<script type="application/ld+json">'+json.dumps(d)+'</script>')[0]
   self.assertEqual(v['price'],849900);self.assertTrue(v['stock'])
 def test_seller_queries_not_combined(self):self.assertFalse(safe('https://btech.com/ar/p/abc?offering_id=xyz'))

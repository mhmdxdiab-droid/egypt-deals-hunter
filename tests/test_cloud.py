import unittest
from types import SimpleNamespace
from cloud import drops
class DropTests(unittest.TestCase):
 def v(self,price,stock=1,sku='a'):return SimpleNamespace(sku=sku,price=price,stock=stock)
 def test_small_drop(self):self.assertEqual(len(drops({'a':10000},[self.v(9999)])),1)
 def test_equal_and_increase(self):self.assertEqual(drops({'a':10000},[self.v(10000),self.v(11000)]),[])
 def test_first_sighting(self):self.assertEqual(drops({},[self.v(9000)]),[])
 def test_unknown_stock(self):self.assertEqual(drops({'a':10000},[self.v(9000,None),self.v(9000,0)]),[])
 def test_other_variant(self):self.assertEqual(drops({'a':10000},[self.v(9000,sku='b')]),[])

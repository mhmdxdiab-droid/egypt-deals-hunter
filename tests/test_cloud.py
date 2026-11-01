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

from unittest.mock import patch
from direct import Store
from cloud import probe
class ProbeTests(unittest.TestCase):
 def test_sent_once(self):
  s=Store(':memory:')
  with patch('cloud.bot') as send:
   probe(s,'token','123');probe(s,'token','123')
   self.assertEqual(send.call_count,1)
  self.assertEqual(s.db.execute("SELECT v FROM state WHERE k='telegram_probe_v1'").fetchone()[0],'sent')
 def test_uncertain_not_retried(self):
  s=Store(':memory:')
  with patch('cloud.bot',side_effect=RuntimeError('hidden')) as send:
   with self.assertRaises(RuntimeError):probe(s,'token','123')
   probe(s,'token','123')
   self.assertEqual(send.call_count,1)
  self.assertEqual(s.db.execute("SELECT v FROM state WHERE k='telegram_probe_v1'").fetchone()[0],'uncertain')

from cloud import select_urls
class SchedulerTests(unittest.TestCase):
 def setUp(self):self.s=Store(':memory:')
 def add(self,n,known=False,due=0):
  with self.s.db:self.s.db.execute('INSERT INTO pages(url,last_ok,next_check) VALUES(?,?,?)',(f'https://mobaco.com/en/item-{n}',n if known else None,due))
 def test_capacity_and_both_pools(self):
  for n in range(20):self.add(n,True);self.add(n+100)
  u=select_urls(self.s)
  self.assertEqual(len(u),12)
  self.assertEqual(sum(int(x.rsplit('-',1)[1])<100 for x in u),6)
 def test_only_known_uses_full_capacity(self):
  for n in range(20):self.add(n,True)
  self.assertEqual(len(select_urls(self.s)),12)
 def test_not_due_is_skipped(self):
  self.add(1,True,99999999999)
  self.assertEqual(select_urls(self.s),[])

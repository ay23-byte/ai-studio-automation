import unittest
from core.news_editorial import cluster_candidates, rank_clusters, build_review_packets
class NewsEditorialTests(unittest.TestCase):
 def setUp(self):
  self.items=[
   {'id':'a','title':'India launches new satellite mission','url':'https://a.example/x','publisher':'A','published':''},
   {'id':'b','title':'India satellite mission launch today','url':'https://b.example/y','publisher':'B','published':''},
   {'id':'c','title':'Local cricket final result','url':'https://c.example/z','publisher':'C','published':''}]
 def test_related_headlines_cluster(self):
  groups=cluster_candidates(self.items,threshold=0.2)
  self.assertEqual(len(groups),2)
 def test_google_news_redirects_use_real_publisher_domains(self):
  items=[
   {'id':'g1','title':'India launches new satellite mission','url':'https://news.google.com/rss/articles/one',
    'publisher_url':'https://www.reuters.com/world/india/one','publisher':'Reuters','published':''},
   {'id':'g2','title':'India satellite mission launch today','url':'https://news.google.com/rss/articles/two',
    'publisher_url':'https://www.thehindu.com/science/space/two','publisher':'The Hindu','published':''}]
  groups=cluster_candidates(items,threshold=0.2)
  self.assertEqual(len(groups),1)
  self.assertEqual(groups[0]['publisher_domain_count'],2)
  self.assertEqual(groups[0]['publishers'],['reuters.com','thehindu.com'])

 def test_rank_is_explicitly_not_reliability(self):
  ranked=rank_clusters(cluster_candidates(self.items,0.2),self.items)
  self.assertIn('Not a fact-check',ranked[0]['score_explanation'])
 def test_review_packet_starts_pending(self):
  packets=build_review_packets(self.items)
  self.assertTrue(all(x['review_status']=='pending_human_verification' for x in packets))
  self.assertTrue(packets[0]['review_checklist'])
if __name__=='__main__': unittest.main()
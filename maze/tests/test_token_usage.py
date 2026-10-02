import unittest
from types import SimpleNamespace
from token_usage import UsageMeter

def config(**kw):
    return SimpleNamespace(model='gpt-4o-mini', base_url=None, input_price=None, cached_input_price=None, output_price=None, **kw)

def response():
    return {'model':'gpt-4o-mini','usage':{'input_tokens':1000,'output_tokens':200,'total_tokens':1200,'input_tokens_details':{'cached_tokens':400},'output_tokens_details':{'reasoning_tokens':100}}}

class UsageTests(unittest.TestCase):
    def test_cached_and_reasoning_not_double_counted(self):
        totals=UsageMeter(config()).record(response(),'agent')
        self.assertEqual(totals['total_tokens'],1200)
        self.assertEqual(totals['uncached_input_tokens'],600)
        self.assertEqual(totals['reasoning_tokens'],100)
        self.assertAlmostEqual(totals['estimated_cost_usd'],.00024)
    def test_gpt35_snapshot_has_standard_prices_without_cache_discount(self):
        data=response(); data['model']='gpt-3.5-turbo-0125'
        data['usage']['input_tokens_details']['cached_tokens']=0
        totals=UsageMeter(config()).record(data,'agent')
        self.assertEqual(totals['unpriced_requests'],0)
        self.assertAlmostEqual(totals['estimated_cost_usd'],.0008)
        data['usage']['input_tokens_details']['cached_tokens']=400
        totals=UsageMeter(config()).record(data,'agent')
        self.assertIsNone(totals['estimated_cost_usd'])
        self.assertIn('cached-input',totals['calls'][0]['unpriced_reason'])

    def test_probe_usage_carried_forward(self):
        initial=UsageMeter(config()).record(response(),'connection_check')
        totals=UsageMeter(config(),initial).record(response(),'agent')
        self.assertEqual(totals['requests'],2)
        self.assertEqual(totals['input_tokens'],2000)
        self.assertEqual(initial['requests'],1)
    def test_missing_usage_is_unknown(self):
        totals=UsageMeter(config()).record({},'agent')
        self.assertIsNone(totals['estimated_cost_usd'])
        self.assertEqual(totals['missing_usage_requests'],1)
    def test_other_provider_needs_explicit_prices(self):
        cfg=config(); cfg.base_url='http://localhost:1234/v1'
        self.assertIsNone(UsageMeter(cfg).record(response(),'agent')['estimated_cost_usd'])
        cfg.input_price=1; cfg.cached_input_price=.5; cfg.output_price=2
        self.assertAlmostEqual(UsageMeter(cfg).record(response(),'agent')['estimated_cost_usd'],.0012)

if __name__=='__main__': unittest.main()

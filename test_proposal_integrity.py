"""No provider calls: reproduce the factuality and delivery failures from Q-10215."""
import unittest
from unittest.mock import patch
from hub import current_marketing, proposal_spec, business_description
from hub.proposal_integrity import readiness


class ProposalIntegrityTests(unittest.TestCase):
    def test_unknown_is_not_a_gap(self):
        state={'mkt':{q['key']:'Unknown' for q in current_marketing.QUESTIONS}}
        self.assertEqual(current_marketing.suggestions(state),[])
        self.assertEqual(current_marketing.suite_coverage(state)['covered'],[])
        self.assertTrue(current_marketing.suite_coverage(state)['not_measured'])

    def test_confirmed_no_retains_recommendation_without_internal_price(self):
        result=current_marketing.suggestions({'mkt':{'retargeting':'No'}})
        self.assertEqual(len(result),1)
        self.assertNotIn('$4.75',result[0]['detail'])

    def test_prompt_uses_selected_audiences_only(self):
        prompt=proposal_spec.system_prompt({'industry':'Home Services','audiences':['Homeowners']})
        self.assertIn('Homeowners',prompt)
        self.assertNotIn('RV & Camping Enthusiasts',prompt)
        self.assertNotIn('ConsumerView',prompt)
        self.assertIn('Unknown is not No',prompt)

    def test_example_domain_never_reaches_model_or_fetch(self):
        with patch('modules.ads_builder.landing_page.observe') as observe:
            with self.assertRaises(ValueError):business_description.source_context(['https://example.com'])
            observe.assert_not_called()

    def test_unreadable_website_stops_generation(self):
        with patch('modules.ads_builder.landing_page.observe',return_value={'measured':False,'text':''}):
            with self.assertRaises(ValueError):business_description.source_context(['https://business.example.org'])

    def test_website_evidence_is_passed_as_data(self):
        with patch('modules.ads_builder.landing_page.observe',return_value={'measured':True,'url':'https://business.example.org','text':'Repairs and installations. '*20}):
            evidence=business_description.source_context(['https://business.example.org'])
        prompt=business_description.prompt_for(['https://business.example.org'],evidence=evidence)
        self.assertIn('Repairs and installations.',prompt)
        self.assertIn('INSUFFICIENT_EVIDENCE',prompt)

    def test_invalid_plan_and_failed_sections_block_delivery(self):
        state={'client':'QA','items':[{'product':'Programmatic Campaign with Retargeting','category':'DATA TARGETED DISPLAY','dollars':300}], 'draftFailures':[{'id':'areas','title':'Audience'}]}
        issues=readiness(state)
        self.assertTrue(any('minimum' in i for i in issues))
        self.assertTrue(any('Audience' in i for i in issues))

    def test_internal_pricing_blocks_delivery(self):
        state={'client':'QA','items':[{'product':'Pay Per Click','category':'SEARCH ENGINE MARKETING / PAY PER CLICK','dollars':1200}],'sections':[{'kind':'text','title':'Friction','body':'Only $4.75 on the card'}]}
        self.assertTrue(any('internal pricing' in i for i in readiness(state)))

    def test_valid_plan_and_one_time_cost(self):
        self.assertEqual(readiness({'client':'QA','items':[{'product':'Pay Per Click','category':'SEARCH ENGINE MARKETING / PAY PER CLICK','dollars':1200},{'product':'Creative','basis':'one_time','dollars':35}]}),[])

    def test_invalid_package_price_is_a_validation_error(self):
        self.assertTrue(any('finite' in p for p in readiness({'client':'QA','packages':[{'lines':[{'amt':'invalid'}]}]})))

    def test_zip_verification_survives_normalization(self):
        from hub.target_areas import normalize_area
        area=normalize_area({'origin':'Columbus','zips':'43215','zipSource':'Approved platform map','zipVerified':True})
        self.assertTrue(area['zipVerified'])
        self.assertEqual(area['zipSource'],'Approved platform map')

    def test_home_services_does_not_match_rv_industry(self):
        self.assertFalse(any('RV & Camping' in s for s in proposal_spec.audience_segments_for('Home Services')))


if __name__=='__main__':unittest.main()

import copy, runpy, unittest
validate = runpy.run_path('modules/io_builder/media_mix.py')['validate_recommendation']
class MediaMixValidation(unittest.TestCase):
 def setUp(self):
  self.intake={'monthly_budget':1000,'goals':['Leads'],'geography':'Columbus','campaign_duration':30,'selected_products':[{'product':'Display'},{'product':'Search'}]}
  self.result={'summary':'A balanced plan','suggested_allocations':[{'product':'Display','monthly_budget':400,'percent':40,'reason':'Reach'},{'product':'Search','monthly_budget':600,'percent':60,'reason':'Intent'}]}
 def test_valid(self):
  self.assertEqual(len(validate(self.result,self.intake)['suggested_allocations']),2)
 def test_advice_without_allocations(self):
  self.assertEqual(validate({'summary':'Need more details','warnings':['Budget missing']},{})['suggested_allocations'],[])
 def test_wrong_shapes(self):
  for value in [None,[],42,{'summary':'Plan','suggested_allocations':{}},{'summary':'Plan','warnings':'Warning'}]:
   with self.subTest(value=value),self.assertRaises(ValueError):validate(value,self.intake)
 def test_invalid_amounts(self):
  for amount in [-1,float('inf'),float('nan'),True,'400',400.001]:
   result=copy.deepcopy(self.result);result['suggested_allocations'][0]['monthly_budget']=amount
   with self.subTest(amount=amount),self.assertRaises(ValueError):validate(result,self.intake)
 def test_invalid_product_or_total(self):
  for changes in [{'product':'Unknown'},{'product':'Search'},{'monthly_budget':399,'percent':39.9},{'percent':41}]:
   result=copy.deepcopy(self.result);result['suggested_allocations'][0].update(changes)
   with self.subTest(changes=changes),self.assertRaises(ValueError):validate(result,self.intake)
 def test_missing_line(self):
  self.result['suggested_allocations'].pop()
  with self.assertRaises(ValueError):validate(self.result,self.intake)
 def test_ambiguous_product(self):
  self.intake['selected_products'].append({'product':'Display'})
  with self.assertRaises(ValueError):validate(self.result,self.intake)
 def test_missing_context(self):
  self.intake['goals']=[]
  with self.assertRaises(ValueError):validate(self.result,self.intake)
 def test_zero_duration(self):
  self.intake['campaign_duration']={'days':0}
  with self.assertRaises(ValueError):validate(self.result,self.intake)
 def test_scheduled_budget(self):
  self.intake['selected_products'][0]['budget_changes']=[{'budget':200}]
  with self.assertRaises(ValueError):validate(self.result,self.intake)

class MediaMixRoute(unittest.TestCase):
 def route(self, response, intake=None):
  import ast, json, re
  from types import SimpleNamespace
  from pathlib import Path
  tree=ast.parse(Path('modules/io_builder/app.py').read_text(encoding='utf-8'))
  route=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='media_mix_recommendation')
  route.decorator_list=[]
  namespace={'request':SimpleNamespace(get_json=lambda **kw: intake if intake is not None else {}),'jsonify':lambda value:value,'json':json,'re':re,'validate_recommendation':validate,'_openai_response':lambda *args,**kwargs:response}
  exec(compile(ast.Module(body=[route],type_ignores=[]),'route-test','exec'),namespace)
  return namespace['media_mix_recommendation']()
 def test_advice_response(self):
  self.assertTrue(self.route('{"summary":"Need campaign details"}')['ok'])
 def test_bad_ai_responses_fail_closed(self):
  for response in ['not JSON','[]','{"summary":"Plan","suggested_allocations":{}}']:
   with self.subTest(response=response):
    body,status=self.route(response);self.assertEqual(status,502);self.assertFalse(body['ok'])
 def test_bad_intake(self):
  body,status=self.route('{}',['bad']);self.assertEqual(status,400)

if __name__=='__main__':unittest.main()

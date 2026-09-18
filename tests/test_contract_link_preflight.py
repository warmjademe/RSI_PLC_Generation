import copy
import unittest

from experiments.historical_assets_study.contract_link_preflight import assert_training_link_intervention
from our_method.source_contract_links import link_requirements, context_texts
from tests import test_source_contract_links as source_fixture


def banks():
    program=source_fixture.SourceContractLinksTest().program()
    evidence={'task_id':program['task_id'],'binding':{'p0':'Out'},'output_witnesses':[{'requirement_ids':['R2']}],
              'source_requirements':[program['metadata']['requirements'][0]['text']]}
    old=[{**program,'kind':'verified_program'},
         {'id':'repair-example','representation_version':'training_repair_slices_v2','output_role':'p0',
          'after_st':'p0 := TRUE;','evidence':[evidence]}]
    new=copy.deepcopy(old);e=new[1]['evidence'][0]
    links=link_requirements(program,'Out',e['output_witnesses'])
    e.update(source_contract_links=links,source_requirements=context_texts(links,e['output_witnesses']))
    return new,old


class ContractLinkPreflightTest(unittest.TestCase):
    def test_original_sources_reconstruct_candidate_links(self):
        new,old=banks();before=copy.deepcopy((new,old))
        self.assertEqual(assert_training_link_intervention(new,old),
                         {'preserved_catalog_records':1,'preserved_code_role_state_assets':1,
                          'source_contracts_reconstructed':1,'original_training_programs':1})
        self.assertEqual((new,old),before)

    def test_rejects_altered_code_catalog_and_invented_links(self):
        for mutation in ('code','catalog','link','text','evidence'):
            new,old=banks()
            if mutation=='code':new[1]['after_st']='p0 := FALSE;'
            elif mutation=='catalog':new[0]['metadata']['requirements'][0]['text']='Changed source'
            elif mutation=='link':new[1]['evidence'][0]['source_contract_links']['requirements'][0]['requirement']['text']='Invented condition'
            elif mutation=='text':new[1]['evidence'][0]['source_requirements'].append('Test-derived advice')
            else:new[1]['evidence'][0]['extra']='unexpected'
            with self.assertRaises(ValueError,msg=mutation):assert_training_link_intervention(new,old)

    def test_rejects_added_and_reordered_assets(self):
        new,old=banks()
        with self.assertRaises(ValueError):assert_training_link_intervention(new+[new[1]],old)
        with self.assertRaises(ValueError):assert_training_link_intervention(list(reversed(new)),old)


if __name__ == '__main__':unittest.main()

import copy
import unittest

from experiments.historical_assets_study.audit_metadata_projection import separate,restore
from our_method.repair_retrieval import retrieve
from tests.test_repair_retrieval import bank,task


class AuditMetadataProjectionTests(unittest.TestCase):
    def memory(self):
        return retrieve(bank(),task(),[],{'repair_reference_view':'after_only','repair_selection_view':'contrast'})

    def test_projection_is_invertible_and_preserves_semantic_fields(self):
        memory=self.memory();original=copy.deepcopy(memory);visible,audit=separate(memory)
        self.assertEqual(memory,original)
        self.assertEqual(restore(visible,audit),memory)
        self.assertNotIn('selection_audit',visible)
        for old,new in zip(memory['items'],visible['items']):
            for key in old:
                if key not in ('source_record_sha256','source_projection'):
                    self.assertEqual(new[key],old[key])

    def test_changes_to_code_witness_or_roles_are_rejected(self):
        for key,value in [('after_st',''),('roles',[]),('source_failure_witness',{})]:
            visible,audit=separate(self.memory());visible['items'][0][key]=value
            with self.assertRaisesRegex(ValueError,'projection content changed'):restore(visible,audit)

    def test_audit_cannot_be_attached_to_another_source(self):
        visible,audit=separate(self.memory());audit['items'][0]['source_repair_id']='different source'
        with self.assertRaisesRegex(ValueError,'source identity changed'):restore(visible,audit)

    def test_empty_asset_control_is_unchanged(self):
        memory=retrieve(bank(),task(),[],{'use_code_memory':False})
        visible,audit=separate(memory)
        self.assertEqual(visible,memory)
        self.assertEqual(restore(visible,audit),memory)


if __name__=='__main__':unittest.main()

import unittest

from experiments.historical_assets_study.compiler_repair_diagnosis import compiler_messages, structural_change


def program(body,storage='VAR',kind='BOOL'):
    return f'FUNCTION_BLOCK Example\nVAR_INPUT enabled:BOOL; END_VAR\n{storage} held:{kind}; END_VAR\n{body}\nEND_FUNCTION_BLOCK'


class CompilerRepairDiagnosisTests(unittest.TestCase):
    def test_identity_difference_requires_internal_bool_and_unchanged_guard(self):
        old='IF enabled THEN ELSE END_IF;';new='IF enabled THEN ELSE held:=held; END_IF;'
        self.assertTrue(structural_change(program(old),program(new))['identity_only_change'])
        for storage,kind in [('VAR_OUTPUT','BOOL'),('VAR','INT')]:
            self.assertFalse(structural_change(program(old,storage,kind),program(new,storage,kind))['identity_only_change'])
        self.assertFalse(structural_change(program(old),program(new.replace('IF enabled','IF NOT enabled')))['identity_only_change'])

    def test_empty_else_removal_is_not_a_change_in_abstract_effects(self):
        old='IF enabled THEN held:=TRUE; ELSE END_IF;';new='IF enabled THEN held:=TRUE; END_IF;'
        result=structural_change(program(old),program(new))
        self.assertTrue(result['semantic_ast_equal'])
        self.assertFalse(result['identity_only_change'])

    def test_unsupported_before_program_is_retained_as_unparsed(self):
        result=structural_change(program('missing:=TRUE;'),program('held:=TRUE;'))
        self.assertFalse(result['both_programs_parseable'])
        self.assertIn('undeclared',result['parser_rejection'])

    def test_compiler_messages_keep_diagnostics_without_source_paths(self):
        gates=[{'name':'compiler','status':'fail','evidence':[{'kind':'compile_error',
            'summary':"/private/source.st:10: error: no statement defined after 'ELSE'.\n1 error(s) found."}]}]
        self.assertEqual(compiler_messages(gates),["no statement defined after 'ELSE'."])
        gates[0]['status']='unknown'
        self.assertEqual(compiler_messages(gates),[])


if __name__=='__main__':unittest.main()

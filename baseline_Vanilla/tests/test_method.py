import unittest
from tests.test_comparison import ComparisonTests

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ComparisonTests(name) for name in ['test_vanilla_has_no_training_context', 'test_no_sixth_candidate_after_five_failures', 'test_missing_tool_does_not_trigger_code_repair'])

import unittest
from tests.test_comparison import ComparisonTests

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ComparisonTests(name) for name in ['test_our_method_only_uses_repairs_from_successful_episodes', 'test_our_method_oversized_bank_does_not_rescan_all_contracts_per_rejection', 'test_public_loader_never_reads_evaluator'])

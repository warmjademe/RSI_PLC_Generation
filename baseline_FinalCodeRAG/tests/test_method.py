import unittest
from tests.test_comparison import ComparisonTests

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ComparisonTests(name) for name in ['test_final_code_retrieval_changes_with_query', 'test_memory_tampering_and_query_leakage_are_rejected'])

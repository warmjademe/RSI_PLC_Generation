import unittest
from tests.test_comparison import ComparisonTests

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ComparisonTests(name) for name in ['test_fixed_fewshot_is_frozen_before_query'])

import unittest
from tests.test_comparison import ComparisonTests

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ComparisonTests(name) for name in ['test_msce_evidence_validation_and_value_backfill', 'test_msce_training_does_not_invent_skill_gain'])

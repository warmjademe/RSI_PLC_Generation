import unittest
from our_method.code_projection import without_comments


class CodeProjectionTests(unittest.TestCase):
    def test_comments_cannot_join_identifiers(self):
        self.assertEqual(without_comments('A(* note *)B := TRUE; // note\n'), 'A B := TRUE;  \n')

    def test_literals_and_doubled_or_dollar_escaped_quotes_are_preserved(self):
        code="X := '(* data *)//'; Y := 'it''s'; Z := '$\' // still a literal'; (* remove *)"
        result=without_comments(code)
        self.assertEqual(result,code[:code.index('(* remove *)')]+' ')

    def test_nested_comments_and_line_numbers(self):
        self.assertEqual(without_comments('A := (* outer\n(* inner *)\nend *) 1;'), 'A :=  \n\n 1;')

    def test_unknown_directives_and_malformed_literals_fall_back(self):
        for code in ['(*$VENDOR*) A:=1;', '// @directive\nA:=1;', 'A:=1; (* unterminated', "A:='unterminated"]:
            self.assertIsNone(without_comments(code))


if __name__=='__main__':unittest.main()

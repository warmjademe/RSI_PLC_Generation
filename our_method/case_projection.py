"""Opt-in training parser: integer-variable CASE to ordered IF.

The repair learner must explicitly select scalar_if_case. The transformation keeps
branch bodies and the unmatched path, and never falls through after selection.
Only declared scalar integer selectors and disjoint in-range decimal labels
are supported. Calls, selector expressions, enums, arrays and loops are rejected.

CASE reference: https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_st_instruction_case.html
This reference is not a vendor-validation claim for the transformed program.
"""
import re

from baseline_common.utils import content_hash, object_hash
from .typed_fragments import ID, TOKEN, Parser, Unsupported, declarations, variables


INTEGER_BOUNDS = {
    'SINT': (-128, 127), 'USINT': (0, 255),
    'INT': (-32768, 32767), 'UINT': (0, 65535),
    'DINT': (-2147483648, 2147483647), 'UDINT': (0, 4294967295),
}
CASE_TOKEN = re.compile(r'\.\.|,|' + TOKEN.pattern)


def case_tokens(text):
    result = []
    end = 0
    for match in CASE_TOKEN.finditer(text):
        if text[end:match.start()].strip():
            raise Unsupported('unsupported CASE lexical construct')
        result.append(match.group())
        end = match.end()
    if text[end:].strip():
        raise Unsupported('unsupported CASE trailing token')
    return result


def integer_literal(value):
    if value < 0:
        return ['unary', '-', ['literal', str(-value)]]
    return ['literal', str(value)]


class CaseParser(Parser):
    def __init__(self, values, fields):
        super().__init__(values)
        self.fields = fields
        self.case_sites = []

    def integer(self):
        sign = -1 if self.peek() == '-' else 1
        if self.peek() in ('+', '-'):
            self.take()
        value = self.take()
        if not re.fullmatch(r'\d+', value):
            raise Unsupported('CASE labels must be decimal integer literals')
        return sign * int(value)

    def case_statement(self):
        offset = self.i
        self.take('CASE')
        selector = self.take().casefold()
        info = self.fields.get(selector, {})
        if not ID.fullmatch(selector) or info.get('type') not in INTEGER_BOUNDS:
            raise Unsupported('CASE selector must be a declared scalar integer variable')
        self.take('OF')
        lower, upper = INTEGER_BOUNDS[info['type']]
        used = []
        branches = []
        source_labels = []
        while self.peek() not in ('ELSE', 'END_CASE', ''):
            intervals = []
            while True:
                first = self.integer()
                last = first
                if self.peek() == '..':
                    self.take()
                    last = self.integer()
                if not lower <= first <= last <= upper:
                    raise Unsupported('CASE label range is reversed or outside selector type')
                if any(max(first, a) <= min(last, b) for a, b in used):
                    raise Unsupported('CASE labels overlap')
                used.append((first, last))
                intervals.append([first, last])
                if self.peek() != ',':
                    break
                self.take()
            self.take(':')
            condition = None
            for first, last in intervals:
                if first == last:
                    term = ['binary', '=', ['var', selector], integer_literal(first)]
                else:
                    term = ['binary', 'AND',
                            ['binary', '>=', ['var', selector], integer_literal(first)],
                            ['binary', '<=', ['var', selector], integer_literal(last)]]
                condition = term if condition is None else ['binary', 'OR', condition, term]
            branches.append([condition, self.statements(('ELSE', 'END_CASE'), stop_at_label=True)])
            source_labels.append(intervals)
        if not branches:
            raise Unsupported('CASE must contain a labeled branch')
        otherwise = []
        has_else = self.peek() == 'ELSE'
        if has_else:
            self.take()
            otherwise = self.statements(('END_CASE',))
        self.take('END_CASE')
        if self.peek() == ';':
            self.take()
        self.case_sites.append({'token_offset': offset, 'selector': selector,
                                'selector_type': info['type'], 'branch_intervals': source_labels,
                                'explicit_else': has_else})
        return ['if', branches, otherwise]

    def statements(self, until=(), *, stop_at_label=False):
        result = []
        while self.peek() and self.peek() not in until:
            if stop_at_label and (self.peek() in ('+', '-') or re.fullmatch(r'\d+', self.peek())):
                break
            if self.peek() == ';':
                self.take()
                continue
            if self.peek() == 'CASE':
                result.append(self.case_statement())
            elif self.peek() == 'IF':
                self.take()
                condition = self.expr()
                self.take('THEN')
                branches = [[condition, self.statements(('ELSIF', 'ELSE', 'END_IF'))]]
                while self.peek() == 'ELSIF':
                    self.take()
                    condition = self.expr()
                    self.take('THEN')
                    branches.append([condition, self.statements(('ELSIF', 'ELSE', 'END_IF'))])
                otherwise = []
                if self.peek() == 'ELSE':
                    self.take()
                    otherwise = self.statements(('END_IF',))
                self.take('END_IF')
                if self.peek() == ';':
                    self.take()
                result.append(['if', branches, otherwise])
            else:
                target = self.take()
                if not ID.fullmatch(target):
                    raise Unsupported('scalar target required')
                self.take(':=')
                expression = self.expr()
                self.take(';')
                result.append(['assign', target.casefold(), expression])
        return result


def parsed_case_program(code):
    fields, body = declarations(code)
    parser = CaseParser(case_tokens(body), fields)
    nodes = parser.statements()
    reads, writes = variables(nodes)
    if any(name not in fields for name in reads + writes):
        raise Unsupported('undeclared variable in CASE source')
    if any(fields[name]['direction'] == 'VAR_INPUT' for name in writes):
        raise Unsupported('CASE source writes an input')
    return fields, nodes, {
        'normalization': 'integer_variable_case_to_if_v1',
        'source_code_sha256': content_hash(code),
        'lowered_program_sha256': object_hash({'fields': fields, 'tree': nodes}),
        'case_sites': sorted(parser.case_sites, key=lambda item: item['token_offset']),
        'new_plc_execution': False, 'transfer_efficacy_established': False,
    }

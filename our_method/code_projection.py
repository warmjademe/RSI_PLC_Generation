"""Remove ordinary ST comments from a displayed donor without changing tokens."""
import re


def without_comments(code):
    output=[];i=0;size=len(code)
    while i<size:
        char=code[i]
        if char in "'\"":
            quote=char;output.append(char);i+=1;closed=False
            while i<size:
                char=code[i];output.append(char);i+=1
                if char=='$' and i<size:
                    output.append(code[i]);i+=1
                elif char==quote:
                    if i<size and code[i]==quote:output.append(code[i]);i+=1
                    else:closed=True;break
            if not closed:return None
        elif code.startswith('(*',i):
            # Preserve possible compiler directives instead of interpreting them.
            if re.match(r'\(\*\s*[$@!#]',code[i:]):return None
            depth=1;j=i+2
            while j<size and depth:
                if code.startswith('(*',j):
                    if re.match(r'\(\*\s*[$@!#]',code[j:]):return None
                    depth+=1;j+=2
                elif code.startswith('*)',j):depth-=1;j+=2
                else:j+=1
            if depth:return None
            output.append(' '+('\n'*code[i:j].count('\n')));i=j
        elif code.startswith('//',i):
            end=code.find('\n',i)
            if end<0:end=size
            if re.match(r'//\s*[$@!#]',code[i:end]):return None
            output.append(' ');i=end
        elif char=='{':
            # Pragmas are compiler-specific; keep the original whole donor.
            return None
        else:output.append(char);i+=1
    # Empty lines left by comments have no lexical effect. Strings containing
    # newlines are retained by avoiding a second whitespace rewriting pass.
    return ''.join(output)

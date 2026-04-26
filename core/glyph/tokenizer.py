from dataclasses import dataclass
import re
from typing import List

@dataclass
class Token:
    type: str
    value: str

def tokenize_glyph(source: str) -> List[Token]:
    tokens = []
    # Simple tokenizer for Glyph
    # Legend: @ CODE=Name
    # Header: ~f TD
    # Nodes: A[Label]
    # Edges: -->, -.->, ==>
    # etc.
    
    # Use a combined regex for tokens
    pattern = re.compile(
        r'(?P<legend>@)\s*|'
        r'(?P<header>~[fegscpmg])\s*|'
        r'(?P<direction>TD|TB|LR|RL|BT)\b|'
        r'(?P<edge_thick>==>)|'
        r'(?P<edge_dotted>-.->)|'
        r'(?P<edge_solid>-->)|'
        r'(?P<node_shape>\[|\]|\(|\)|\{\}|\[\/|/\]|\[\(|\)\])|'
        r'(?P<arrow>->>|-->>|-x)|'
        r'(?P<colon>:)|'
        r'(?P<equal>=)|'
        r'(?P<comma>,)|'
        r'(?P<bracket>\{|\})|'
        r'(?P<whitespace>\s+)|'
        r'(?P<identifier>[a-zA-Z0-9_.\$+\-#*~]+)|'
        r'(?P<text>".*?"|\'.*?\')|'
        r'(?P<other>.)'
    )
    
    for match in pattern.finditer(source):
        kind = match.lastgroup
        value = match.group(kind)
        if kind == 'whitespace':
            continue
        tokens.append(Token(kind, value))
    return tokens

def tokenize_mermaid(source: str) -> List[Token]:
    # Similar to glyph but adjusted for mermaid keywords
    pattern = re.compile(
        r'(?P<keyword>flowchart|graph|erDiagram|sequenceDiagram|classDiagram|mindmap|pie)\b|'
        r'(?P<direction>TD|TB|LR|RL|BT)\b|'
        r'(?P<edge_thick>==>)|'
        r'(?P<edge_dotted>-.->)|'
        r'(?P<edge_solid>-->)|'
        r'(?P<node_shape>\[|\]|\(|\)|\{\}|\[\/|/\]|\[\(|\)\])|'
        r'(?P<arrow>->>|-->>|-x)|'
        r'(?P<colon>:)|'
        r'(?P<equal>=)|'
        r'(?P<comma>,)|'
        r'(?P<bracket>\{|\})|'
        r'(?P<whitespace>\s+)|'
        r'(?P<identifier>[a-zA-Z0-9_.\$+\-#*~]+)|'
        r'(?P<text>".*?"|\'.*?\')|'
        r'(?P<other>.)'
    )
    
    tokens = []
    for match in pattern.finditer(source):
        kind = match.lastgroup
        value = match.group(kind)
        if kind == 'whitespace':
            continue
        tokens.append(Token(kind, value))
    return tokens

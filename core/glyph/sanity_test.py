import sys
import os
sys.path.insert(0, '/Users/emmahyde/projects/memesis')
from core.glyph import GlyphParser, MermaidParser, MermaidTranspiler, GlyphTranspiler

def test_round_trip():
    parser = GlyphParser()
    source = """~f TD
A[Start] --> B{Decision}
B -->|Yes| C[End]"""

    ast = parser.parse(source)
    transpiler = MermaidTranspiler()
    mermaid = transpiler.transpile(ast)
    print("Mermaid output:")
    print(mermaid)

    reverse = MermaidParser()
    ast2 = reverse.parse(mermaid)
    transpiler2 = GlyphTranspiler()
    glyph = transpiler2.transpile(ast2)
    print("\nGlyph output:")
    print(glyph)

    assert ast.type == ast2.type
    print("\nRound-trip OK!")

if __name__ == "__main__":
    test_round_trip()

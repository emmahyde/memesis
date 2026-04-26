import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from core.glyph import GlyphParser, MermaidParser, MermaidTranspiler, GlyphTranspiler, TokenCounter


def test_flowchart_basic_roundtrip():
    parser = GlyphParser()
    glyph = """~f TD
A[Start]→B{Is it?}
B→|Y|C[OK]
B→|N|D[End]"""
    ast = parser.parse(glyph)
    assert ast.type == "flowchart"
    assert ast.direction == "TD"

    mermaid = MermaidTranspiler().transpile(ast)
    assert "flowchart TD" in mermaid
    assert "A[Start]" in mermaid
    assert "B{Is it?}" in mermaid

    ast2 = MermaidParser().parse(mermaid)
    assert ast2.type == "flowchart"


def test_chain_expansion():
    parser = GlyphParser()
    glyph = "~f LR\nA[In]→B{Check}→C[OK]→D[Out]"
    ast = parser.parse(glyph)
    edges = [e for e in ast.elements if hasattr(e, "from_id")]
    assert len(edges) == 3
    assert edges[0].from_id == "A" and edges[0].to_id == "B"
    assert edges[1].from_id == "B" and edges[1].to_id == "C"
    assert edges[2].from_id == "C" and edges[2].to_id == "D"


def test_erd_compact_attrs():
    parser = GlyphParser()
    glyph = """~e
CUSTOMER{id PK,name,email,cat}
ORDER{id PK,cid FK,f total,s status}
CUSTOMER||--o{ORDER:places"""
    ast = parser.parse(glyph)
    entities = [e for e in ast.elements if hasattr(e, "name")]
    assert len(entities) == 2

    cust = entities[0]
    assert cust.name == "CUSTOMER"
    assert cust.attributes[0].name == "id"
    assert cust.attributes[0].constraints == ["PK"]
    assert cust.attributes[1].name == "name"

    mermaid = MermaidTranspiler().transpile(ast)
    assert "erDiagram" in mermaid
    assert "CUSTOMER" in mermaid


def test_erd_explicit_types():
    parser = GlyphParser()
    glyph = "~e\nUSER{s id PK,s n,i a}"
    ast = parser.parse(glyph)
    ent = [e for e in ast.elements if hasattr(e, "name")][0]
    assert ent.attributes[0].type == "s"
    assert ent.attributes[0].name == "id"
    assert ent.attributes[1].type == "s"
    assert ent.attributes[1].name == "n"
    assert ent.attributes[2].type == "i"
    assert ent.attributes[2].name == "a"


def test_legend_resolution():
    parser = GlyphParser()
    glyph = """@ C=CUSTOMER @ O=ORDER
~e
C{id PK}
O{id PK}
C||--o{O:places"""
    ast = parser.parse(glyph)
    ents = [e for e in ast.elements if hasattr(e, "name")]
    assert ents[0].name == "CUSTOMER"
    assert ents[1].name == "ORDER"
    rels = [e for e in ast.elements if hasattr(e, "from_entity")]
    assert rels[0].from_entity == "CUSTOMER"
    assert rels[0].to_entity == "ORDER"


def test_sequence_inferred_actors():
    parser = GlyphParser()
    glyph = """~s
Alice->>Bob:Hello
Bob->>Alice:Bye"""
    ast = parser.parse(glyph)
    msgs = [e for e in ast.elements if hasattr(e, "from_actor")]
    assert len(msgs) == 2
    assert msgs[0].from_actor == "Alice"
    assert msgs[0].to_actor == "Bob"

    mermaid = MermaidTranspiler().transpile(ast)
    assert "sequenceDiagram" in mermaid
    assert "participant Alice" in mermaid
    assert "participant Bob" in mermaid


def test_sequence_loop_block():
    parser = GlyphParser()
    glyph = """~s
A->>B:Start
loop Every minute
A->>B:Ping
end"""
    ast = parser.parse(glyph)
    blocks = [e for e in ast.elements if getattr(e, "shape", None) == "SEQ_BLOCK"]
    assert any(b.id == "loop Every minute" for b in blocks)
    assert any(b.id == "end" for b in blocks)


def test_class_members():
    parser = GlyphParser()
    glyph = "~c\nAnimal{+name,+age,+move()}"
    ast = parser.parse(glyph)
    members = [e for e in ast.elements if hasattr(e, "visibility")]
    assert len(members) == 3
    assert members[0].visibility == "+"
    assert members[0].name == "name"
    assert members[1].name == "age"
    assert members[2].name == "move"


def test_subgraph_compact():
    parser = GlyphParser()
    glyph = """~f TD
{Auth
A→B
B→C
}
C→D"""
    ast = parser.parse(glyph)
    subs = [e for e in ast.elements if hasattr(e, "name")]
    assert len(subs) == 1
    assert subs[0].name == "Auth"
    edges = [e for e in subs[0].elements if hasattr(e, "from_id")]
    assert len(edges) == 2


def test_empty_diagram():
    parser = GlyphParser()
    ast = parser.parse("~f TD")
    assert ast.type == "flowchart"
    assert ast.direction == "TD"
    assert len(ast.elements) == 0


def test_token_counter_functional():
    counter = TokenCounter()
    m = counter.count_mermaid("flowchart TD\n    A --> B")
    g = counter.count_glyph("~f TD\nA\u2192B")
    assert m > g


def test_mermaid_to_glyph_roundtrip():
    mermaid = """flowchart TD
    A[Start] --> B{Decision}
    B -->|Yes| C[OK]
"""
    ast = MermaidParser().parse(mermaid)
    glyph = GlyphTranspiler().transpile(ast)
    assert "~f TD" in glyph
    assert "A" in glyph
    assert "B" in glyph


def test_flowchart_chain_vs_nodes():
    parser = GlyphParser()
    glyph = "~f TD\nA→B→C→D"
    ast = parser.parse(glyph)
    nodes = [e for e in ast.elements if hasattr(e, "shape")]
    edges = [e for e in ast.elements if hasattr(e, "from_id")]
    assert len(nodes) == 4
    assert len(edges) == 3
    node_ids = {n.id for n in nodes}
    assert node_ids == {"A", "B", "C", "D"}

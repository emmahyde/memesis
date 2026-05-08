import re
from typing import List, Optional

from .glyph_ast import (
    DiagramAST, Node, Edge, Entity, Attribute,
    Relationship, SequenceMessage, Subgraph, ClassMember,
)


class MermaidParser:
    """Parse Mermaid source back into canonical AST for round-trip tests."""

    def parse(self, source: str) -> DiagramAST:
        lines = source.strip().splitlines()
        elements: list = []
        diag_type = "flowchart"
        direction = None
        in_subgraph = None

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("```"):
                continue

            if line in ("flowchart TD", "flowchart LR", "flowchart RL", "flowchart BT", "flowchart TB"):
                parts = line.split()
                diag_type = "flowchart"
                direction = parts[1]
                continue
            if line == "erDiagram":
                diag_type = "erDiagram"
                continue
            if line == "sequenceDiagram":
                diag_type = "sequenceDiagram"
                continue
            if line == "classDiagram":
                diag_type = "classDiagram"
                continue

            if line.startswith("subgraph"):
                name = line[len("subgraph"):].strip()
                in_subgraph = Subgraph(name=name)
                continue
            if line == "end" and in_subgraph:
                elements.append(in_subgraph)
                in_subgraph = None
                continue

            parsed = self._parse_line(line, diag_type)
            if in_subgraph is not None:
                in_subgraph.elements.extend(parsed)
            else:
                elements.extend(parsed)

        return DiagramAST(type=diag_type, direction=direction, elements=elements)

    def _parse_line(self, line: str, diag_type: str):
        if diag_type in ("flowchart", "graph"):
            return self._parse_flow_line(line)
        if diag_type == "erDiagram":
            return self._parse_erd_line(line)
        if diag_type == "sequenceDiagram":
            return self._parse_seq_line(line)
        if diag_type == "classDiagram":
            return self._parse_class_line(line)
        return []

    def _parse_flow_line(self, line: str) -> List:
        elements: list = []
        arrow_pats = ["==>", "-.->", "-->", "==", "-.", "-"]
        arrow_res = [re.compile(re.escape(p) + r'\s*') for p in arrow_pats]

        def find_arrow(text):
            best = -1
            best_s = None
            for pat in arrow_res:
                m = pat.search(text)
                if m and (best == -1 or m.start() < best):
                    best = m.start()
                    best_s = m.group(0)
            return best, best_s

        pos, style = find_arrow(line)
        if pos == -1:
            node = self._try_node(line)
            if node:
                elements.append(node)
            return elements

        left = line[:pos].strip()
        right = line[pos + len(style):].strip()

        from_node = self._try_node(left)
        to_node = self._try_node(right)

        if from_node:
            elements.append(from_node)
        if to_node:
            elements.append(to_node)

        edge_label = None
        edge_style = style.strip()
        for vert in arrow_pats:
            if style.strip().startswith(vert):
                edge_style = vert if vert in ("==>", "-.->", "-->") else ("-->" if vert == "-" else vert)
                break

        elements.append(Edge(
            from_id=from_node.id if from_node else left.split("[")[0].split("{")[0].split("(")[0].strip(),
            to_id=to_node.id if to_node else right.split("[")[0].split("{")[0].split("(")[0].strip(),
            style=edge_style,
            label=edge_label,
        ))
        return elements

    def _try_node(self, text: str) -> Optional[Node]:
        text = text.strip()
        if not text:
            return None
        for pat, shape in [
            (r'\[(.*?)\]', "[]"),
            (r'\((.*?)\)', "()"),
            (r'\{\{(.*?)\}\}', "{{}}"),
            (r'\{(.*?)\}', "{}"),
            (r'\(\((.*?)\)\)', "(())"),
            (r'\[/(.*?)/\]', "[/ /]"),
            (r'\[\((.*?)\)\]', "[()]"),
        ]:
            m = re.search(pat, text)
            if m:
                nid = re.match(r'([a-zA-Z0-9_]+)', text)
                return Node(id=nid.group(1) if nid else text, shape=shape, label=m.group(1))
        if re.match(r'[a-zA-Z0-9_]+$', text):
            return Node(id=text)
        return None

    def _parse_erd_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()
        if line == "}":
            return elements
        if line.endswith("{"):
            name = line[:-1].strip()
            elements.append(Entity(name=name))
            return elements
        rel = re.match(
            r'(\w+)\s+(\|\|--o\{|\|\|--\|\||\}o--o\{|\|o--o\{|\|\|--\|o|\}o--\|\|'
            r'\|\|--\{\|)\s+(\w+)(?:\s*:\s*(.*))?',
            line
        )
        if rel:
            elements.append(Relationship(
                from_entity=rel.group(1),
                to_entity=rel.group(3),
                cardinality=rel.group(2),
                label=rel.group(4)
            ))
            return elements
        attr = re.match(r'(\w+)\s+(\w+)(.*)', line)
        if attr:
            cands = attr.group(3).strip().split()
            constraints = [c for c in cands if c in ("PK", "FK", "UK", "NN")]
            elements.append(Attribute(
                name=attr.group(2), type=attr.group(1), constraints=constraints
            ))
        return elements

    def _parse_seq_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()
        m = re.match(
            r'(\w+)(->>|-->>|-x|--x|->|-\))(\w+):\s*(.*)',
            line
        )
        if m:
            elements.append(SequenceMessage(
                from_actor=m.group(1), to_actor=m.group(3),
                arrow_type=m.group(2), message=m.group(4)
            ))
        return elements

    def _parse_class_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()
        if line.startswith("class "):
            rest = line[len("class "):].strip()
            m = re.match(r'(\w+)\s*\{(.*?)\}', rest)
            if m:
                elements.append(Node(id=m.group(1), shape="CLASS", label=m.group(1)))
                for mem in self._extract_members(m.group(2)):
                    elements.append(mem)
            else:
                elements.append(Node(id=rest, shape="CLASS"))
            return elements
        inh = re.match(r'(\w+)\s*(<\|--|\*--|o--|\.\.|\.\.\|>|--\*|--)\s*(\w+)', line)
        if inh:
            elements.append(Edge(
                from_id=inh.group(1), to_id=inh.group(3),
                style=inh.group(2),
            ))
            return elements
        for mem in self._extract_members(line):
            elements.append(mem)
        return elements

    def _extract_members(self, body: str) -> List[ClassMember]:
        members: list = []
        for seg in body.split("\n"):
            seg = seg.strip()
            if not seg or seg.startswith("class "):
                continue
            m = re.match(r'([+\-#~*\$])?([a-zA-Z0-9_]+)(?:\((.*?)\))?(?::(\w+))?$', seg)
            if m:
                members.append(ClassMember(
                    visibility=m.group(1) or "+",
                    name=m.group(2),
                    type=m.group(4),
                ))
        return members

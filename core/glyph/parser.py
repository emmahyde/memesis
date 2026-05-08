import re
from typing import List, Optional, Tuple

from .glyph_ast import (
    DiagramAST, Node, Edge, Entity, Attribute,
    Relationship, SequenceMessage, Subgraph, ClassMember,
)


class GlyphParser:
    """Parse Glyph source into canonical AST.

    Grammar (line-based):
        @ CODE=Value               legend entry
        ~[f|e|s|c|g] [DIR]        diagram header
        NODE_CHAIN                 A[s]→B{d}→C[e]
        ENTITY { ATTRS }           compact ERD entity
        RELATIONSHIP               A ||--o{ B : label
        SEQ_MSG                    Actor->>Actor:msg
        { Name ... }               subgraph shorthand
    """

    def __init__(self):
        self.legend: dict[str, str] = {}
        self._arrow_pats = [
            ("==>", "==>"),
            ("-.->", "-.->"),
            ("-->", "-->"),
            ("==", "==>"),
            ("-.", "-.->"),
            ("-", "-->"),
            ("\u21d2", "==>"),
            ("\u21e2", "-.->"),
            ("\u2192", "-->"),
        ]
        self._arrow_res = [re.compile(re.escape(p) + r'\s*') for p, _ in self._arrow_pats]

    def parse(self, source: str) -> DiagramAST:
        self.legend.clear()
        lines = source.strip().splitlines()
        elements: list = []
        diag_type = "flowchart"
        direction = None
        in_subgraph = None

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("@"):
                self._parse_legend(line)
                continue
            if line.startswith("~"):
                diag_type, direction = self._parse_header(line)
                continue
            if line.startswith("{"):
                name = line[1:].strip()
                in_subgraph = Subgraph(name=name)
                continue
            if line == "}":
                if in_subgraph:
                    elements.append(in_subgraph)
                    in_subgraph = None
                continue

            parsed = self._parse_line(line, diag_type)
            if in_subgraph is not None:
                in_subgraph.elements.extend(parsed)
            else:
                elements.extend(parsed)

        type_map = {"f": "flowchart", "e": "erDiagram",
                    "s": "sequenceDiagram", "c": "classDiagram",
                    "g": "graph", "p": "pie", "m": "mindmap"}

        return DiagramAST(
            type=type_map.get(diag_type, "flowchart"),
            direction=direction,
            elements=elements,
        )

    def _resolve(self, text: str) -> str:
        return self.legend.get(text, text)

    def _parse_legend(self, line: str) -> None:
        body = line[1:].strip()
        for pair in re.split(r'\s+@\s*|\s+', body):
            if "=" in pair:
                code, name = pair.split("=", 1)
                self.legend[code.strip()] = name.strip()

    def _parse_header(self, line: str) -> Tuple[str, Optional[str]]:
        parts = line[1:].strip().split()
        diag = parts[0] if parts else "f"
        direction = parts[1] if len(parts) > 1 else None
        return diag, direction

    def _parse_line(self, line: str, diag_type: str):
        if diag_type in ("f", "g"):
            return self._parse_flow_line(line)
        if diag_type == "e":
            return self._parse_erd_line(line)
        if diag_type == "s":
            return self._parse_seq_line(line)
        if diag_type == "c":
            return self._parse_class_line(line)
        return []

    def _find_arrow(self, text: str) -> int:
        best = -1
        for pat in self._arrow_res:
            m = pat.search(text)
            if m and (best == -1 or m.start() < best):
                best = m.start()
        return best

    def _build_arrow_re(self):
        esc = sorted((re.escape(p) for p, _ in self._arrow_pats), key=len, reverse=True)
        return re.compile("|".join(esc))

    def _extract_arrows(self, text: str) -> list:
        arrows = []
        for pat in self._arrow_res:
            for m in pat.finditer(text):
                arrows.append((m.start(), m.group().strip()))
        arrows.sort(key=lambda t: t[0])
        return [a[1] for a in arrows]

    def _canonical_arrow(self, raw: str) -> str:
        for glyph, canon in self._arrow_pats:
            if raw.strip().startswith(glyph):
                return canon
        return "-->"

    def _parse_flow_line(self, line: str) -> List:
        elements: list = []
        if self._find_arrow(line) == -1:
            node = self._parse_node(line.strip())
            if node:
                elements.append(node)
            return elements

        arrow_re = self._build_arrow_re()
        arrows = self._extract_arrows(line)
        parts = arrow_re.split(line)

        nodes = [self._parse_node(p.strip()) for p in parts if p.strip()]
        if not nodes:
            return elements

        for i, n in enumerate(nodes):
            if n is None:
                nodes[i] = Node(id=self._resolve(parts[i].strip()))

        for i in range(len(nodes) - 1):
            style = arrows[i] if i < len(arrows) else "-->"
            elements.append(nodes[i])
            elements.append(Edge(from_id=nodes[i].id, to_id=nodes[i+1].id,
                                 style=self._canonical_arrow(style)))
        elements.append(nodes[-1])

        seen = set()
        filtered = []
        for el in elements:
            if isinstance(el, Node):
                if el.id not in seen:
                    seen.add(el.id)
                    filtered.append(el)
            else:
                filtered.append(el)
        return filtered

    def _parse_node(self, fragment: str) -> Optional[Node]:
        fragment = fragment.strip()
        if not fragment:
            return None
        fragment = re.sub(r'\|[^|]+\|', '', fragment).strip()

        lids = [
            (r'\[(.*?)\]', "[]"),
            (r'\((.*?)\)', "()"),
            (r'\{\{(.*?)\}\}', "{{}}"),
            (r'\{(.*?)\}', "{}"),
            (r'\(\((.*?)\)\)', "(())"),
            (r'\[/(.*?)/\]', "[/ /]"),
            (r'\[\((.*?)\)\]', "[()]"),
            (r'>(.*?)\]', ">]"),
        ]

        nid_match = re.match(r'([a-zA-Z0-9_]+)', fragment)
        nid = nid_match.group(1) if nid_match else ""
        label = None
        shape = "[]"
        for pat, shp in lids:
            m = re.search(pat, fragment)
            if m:
                label = m.group(1)
                shape = shp
                break

        if nid or label:
            return Node(id=self._resolve(nid), shape=shape, label=label)

        bare = re.match(r'([a-zA-Z0-9_]+)$', fragment)
        if bare:
            return Node(id=self._resolve(bare.group(1)))
        return None

    def _parse_erd_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()

        rel_match = re.match(
            r'([a-zA-Z0-9_]+)\s*'
            r'(\|\|--o\{|\|\|--\|\||\}o--o\{|\|o--o\{|\|\|--\|o|\}o--\|\|'
            r'\|\|--\{\|)\s*'
            r'([a-zA-Z0-9_]+)'
            r'(?:\s*:\s*(.*))?',
            line
        )
        if rel_match:
            elements.append(Relationship(
                from_entity=self._resolve(rel_match.group(1)),
                to_entity=self._resolve(rel_match.group(3)),
                cardinality=rel_match.group(2),
                label=rel_match.group(4)
            ))
            return elements

        ent_match = re.match(r'([a-zA-Z0-9_]+)\s*\{(.*?)\}', line)
        if ent_match:
            name = self._resolve(ent_match.group(1))
            attrs = self._parse_compact_attrs(ent_match.group(2))
            elements.append(Entity(name=name, attributes=attrs))

        return elements

    _KNOWN_TYPES = {"s", "str", "string", "i", "int", "integer",
                     "f", "float", "d", "double", "b", "bool", "boolean",
                     "dt", "datetime", "date", "t", "text", "j", "json", "u", "uuid"}

    def _parse_compact_attrs(self, body: str) -> List[Attribute]:
        attrs: list = []
        parts = [p.strip() for p in body.split(",") if p.strip()]
        for part in parts:
            attr_type = None
            name = None
            constraints: list = []

            type_prefix = re.match(r'^([a-z]+)[\s:](\w+)', part)
            if type_prefix and type_prefix.group(1) in self._KNOWN_TYPES:
                attr_type = type_prefix.group(1)
                name = type_prefix.group(2)
                rest = part[type_prefix.end():].strip()
                constraints = rest.split() if rest else []
            else:
                name_type = re.match(r'^(\w+)[\s:]([a-z]+)$', part)
                if name_type and name_type.group(2) in self._KNOWN_TYPES:
                    name = name_type.group(1)
                    attr_type = name_type.group(2)
                else:
                    tokens = part.split()
                    if tokens:
                        name = tokens[0]
                        constraints = tokens[1:]

            if name:
                attrs.append(Attribute(name=name, type=attr_type, constraints=constraints))
        return attrs

    def _parse_seq_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()

        if re.match(r'^(loop|alt|par|rect|end|opt|else|and|break|critical|note)\b', line, re.I):
            elements.append(Node(id=line, shape="SEQ_BLOCK"))
            return elements

        msg_match = re.match(
            r'([a-zA-Z0-9_]+)'
            r'(->>|-->>|-x|--x|->|-\))'
            r'([a-zA-Z0-9_]+)'
            r':\s*(.*)',
            line
        )
        if msg_match:
            elements.append(SequenceMessage(
                from_actor=self._resolve(msg_match.group(1)),
                to_actor=self._resolve(msg_match.group(3)),
                arrow_type=msg_match.group(2),
                message=msg_match.group(4)
            ))
        return elements

    def _parse_class_line(self, line: str) -> List:
        elements: list = []
        line = line.strip()

        cls_match = re.match(r'([a-zA-Z0-9_]+)\s*\{(.*?)\}', line)
        if cls_match:
            cls_name = self._resolve(cls_match.group(1))
            elements.append(Node(id=cls_name, shape="CLASS", label=cls_name))
            for mem in self._parse_class_members(cls_match.group(2)):
                elements.append(mem)
            return elements

        inh = re.match(r'(\w+)\s*(<\|--|\*--|o--|\.\.|\.\.\|>|--\*|--)\s*(\w+)', line)
        if inh:
            elements.append(Edge(
                from_id=self._resolve(inh.group(1)),
                to_id=self._resolve(inh.group(3)),
                style=inh.group(2),
            ))
            return elements

        for mem in self._parse_class_members(line):
            elements.append(mem)
        return elements

    def _parse_class_members(self, body: str) -> List[ClassMember]:
        members: list = []
        for segment in body.split(","):
            segment = segment.strip()
            if not segment:
                continue
            m = re.match(
                r'([+\-#~*\$])?([a-zA-Z0-9_]+)(?:\((.*?)\))?(?::(\w+))?$',
                segment
            )
            if m:
                members.append(ClassMember(
                    visibility=m.group(1) or "+",
                    name=m.group(2),
                    type=m.group(4),
                ))
        return members

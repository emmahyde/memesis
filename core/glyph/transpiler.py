from typing import List

from .glyph_ast import (
    DiagramAST, Node, Edge, Entity, Attribute,
    Relationship, SequenceMessage, Subgraph, ClassMember,
)


class MermaidTranspiler:
    """Expand a canonical AST into valid Mermaid source."""

    def transpile(self, ast: DiagramAST) -> str:
        lines: list = []

        header = ast.type
        if ast.direction:
            header += f" {ast.direction}"
        lines.append(header)

        seq_actors = set()
        seq_actors_ordered = []

        for el in ast.elements:
            if isinstance(el, Subgraph):
                lines.append(f"    subgraph {el.name}")
                for sub in el.elements:
                    lines.append(f"        {self._emit(sub)}")
                lines.append("    end")
            elif isinstance(el, SequenceMessage):
                if el.from_actor not in seq_actors:
                    seq_actors.add(el.from_actor)
                    seq_actors_ordered.append(el.from_actor)
                if el.to_actor not in seq_actors:
                    seq_actors.add(el.to_actor)
                    seq_actors_ordered.append(el.to_actor)
                lines.append(f"    {el.from_actor}{el.arrow_type}{el.to_actor}: {el.message}")
            else:
                out = self._emit(el)
                if out:
                    lines.append(f"    {out}")

        for actor in reversed(seq_actors_ordered):
            lines.insert(1, f"    participant {actor}")

        return "\n".join(lines)

    def _emit(self, el):
        if isinstance(el, Node):
            if el.shape == "[]":
                return f"{el.id}[{el.label or el.id}]"
            if el.shape == "()":
                return f"{el.id}({el.label or el.id})"
            if el.shape == "(())":
                return f"{el.id}(({el.label or el.id}))"
            if el.shape == "{}":
                return f"{el.id}{{{el.label or el.id}}}"
            if el.shape == "{{}}":
                return f"{el.id}{{{{{el.label or el.id}}}}}"
            if el.shape == "[/ /]":
                return f"{el.id}[/{el.label or el.id}/]"
            if el.shape == "[()]":
                return f"{el.id}[({el.label or el.id})]"
            if el.shape == ">]":
                return f"{el.id}>{el.label or el.id}]"
            if el.shape == "CLASS":
                return f"class {el.id} {{\n        {el.label or ''}\n    }}"
            if el.shape == "SEQ_BLOCK":
                return el.id
            return f"{el.id}[{el.label or el.id}]"

        if isinstance(el, Edge):
            lbl = f"|{el.label}|" if el.label else ""
            return f"{el.from_id} {el.style}{lbl} {el.to_id}"

        if isinstance(el, Entity):
            lines = [f"{el.name} {{"]
            for a in el.attributes:
                type_str = f"    {a.type or 'string'} {a.name}"
                if a.constraints:
                    type_str += " " + " ".join(a.constraints)
                lines.append(type_str)
            lines.append("    }")
            return "\n".join(lines)

        if isinstance(el, Relationship):
            lbl = f" : {el.label}" if el.label else ""
            return f"{el.from_entity} {el.cardinality} {el.to_entity}{lbl}"

        if isinstance(el, SequenceMessage):
            return f"{el.from_actor}{el.arrow_type}{el.to_actor}: {el.message}"

        if isinstance(el, ClassMember):
            vis = el.visibility or "+"
            type_str = f" {el.type}" if el.type else ""
            return f"    {vis}{el.name}{type_str}"

        return None


class GlyphTranspiler:
    """Compress a canonical AST back into Glyph notation."""

    def transpile(self, ast: DiagramAST) -> str:
        lines: list = []
        type_map = {
            "flowchart": "~f", "erDiagram": "~e",
            "sequenceDiagram": "~s", "classDiagram": "~c",
            "graph": "~g", "pie": "~p", "mindmap": "~m",
        }
        sigil = type_map.get(ast.type, "~f")
        header = f"{sigil} {ast.direction}" if ast.direction else sigil
        lines.append(header)

        for el in ast.elements:
            if isinstance(el, Subgraph):
                lines.append(f"{{{el.name}")
                for sub in el.elements:
                    lines.append(self._emit(sub, compact=True))
                lines.append("}}")
            else:
                out = self._emit(el, compact=True)
                if out:
                    lines.append(out)

        return "\n".join(lines)

    def _emit(self, el, compact=False):
        if isinstance(el, Node):
            if compact and not el.label:
                return el.id
            if el.shape == "[]" and el.label:
                return f"{el.id}[{el.label}]"
            if el.shape == "()" and el.label:
                return f"{el.id}({el.label})"
            if el.shape == "(())" and el.label:
                return f"{el.id}(({el.label}))"
            if el.shape == "{}" and el.label:
                return f"{el.id}{{{el.label}}}"
            return el.id

        if isinstance(el, Edge):
            lbl = f"|{el.label}|" if el.label else ""
            return f"{el.from_id}{lbl}{el.style}{el.to_id}"

        if isinstance(el, Entity):
            attrs = ",".join(self._attr_compact(a) for a in el.attributes)
            return f"{el.name}{{{attrs}}}"

        if isinstance(el, Relationship):
            lbl = f"{el.label}" if el.label else ""
            return f"{el.from_entity}{el.cardinality}{el.to_entity}:{lbl}"

        if isinstance(el, SequenceMessage):
            return f"{el.from_actor}{el.arrow_type}{el.to_actor}:{el.message}"

        if isinstance(el, ClassMember):
            vis = el.visibility or "+"
            type_str = f":{el.type}" if el.type else ""
            return f"{vis}{el.name}{type_str}"

        return None

    def _attr_compact(self, attr: Attribute) -> str:
        if attr.type and attr.type != "string":
            base = f"{attr.type}:{attr.name}"
        else:
            base = attr.name
        if attr.constraints:
            base += " " + " ".join(attr.constraints)
        return base

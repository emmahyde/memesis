from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class Attribute:
    name: str
    type: Optional[str] = None
    constraints: List[str] = field(default_factory=list)


@dataclass
class ClassMember:
    visibility: str
    name: str
    type: Optional[str] = None
    is_static: bool = False
    is_abstract: bool = False


@dataclass
class Node:
    id: str
    shape: str = "[]"
    label: Optional[str] = None


@dataclass
class Edge:
    from_id: str
    to_id: str
    style: str = "-->"
    label: Optional[str] = None


@dataclass
class Entity:
    name: str
    attributes: List[Attribute] = field(default_factory=list)


@dataclass
class Relationship:
    from_entity: str
    to_entity: str
    cardinality: str
    label: Optional[str] = None


@dataclass
class SequenceMessage:
    from_actor: str
    to_actor: str
    message: str
    arrow_type: str = "->>"


@dataclass
class Participant:
    alias: str
    label: Optional[str] = None


@dataclass
class InferredActor:
    name: str


@dataclass
class Subgraph:
    name: str
    elements: List[Union[Node, Edge]] = field(default_factory=list)


@dataclass
class StyleDef:
    selector: str
    css: str


@dataclass
class DiagramAST:
    type: str
    direction: Optional[str] = None
    elements: List = field(default_factory=list)

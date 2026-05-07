# Glyph (γ) — Revised Grammar (ASCII-only, reversible)

## Constraint
ALL notation must use ASCII characters only (no Unicode arrows). Every Glyph diagram MUST decompress back to the exact original Mermaid string.

## Diagram Header
```
~f TD     →  flowchart TD
~e        →  erDiagram
~s        →  sequenceDiagram
~c        →  classDiagram
~g        →  graph TD
~m        →  mindmap
~p        →  pie
```

## Node Shapes (flowchart)
```
[a]       →  [a]
(a)       →  (a)
((a))     →  ((a))
>a]       →  >a]
{a}       →  {a}
{{a}}     →  {{a}}
[/a/]     →  [/a/]
[(a)]     →  [(a)]
```

## Edge Types (flowchart)
```
-->       →  -->
-.->      →  -.->
==>       →  ==>
~~~       →  ~~~
--text-->  → -- text -->
-.text.->  → -. text .->
==text==>  → == text ==>
```

## ERD Relationships
```
||--o{    →  ||--o{
||--||    →  ||--||
}|--o{    →  }|--o{
}o--o{    →  }o--o{
```

## Sequence Commands
```
A->>B:msg      →  A->>B: msg
A-->>B:msg     →  A-->>B: msg
A-xB:msg       →  A-xB: msg
loop cond      →  loop cond
end            →  end
alt cond       →  alt cond
par            →  par
rect #rgb      →  rect rgb(r,g,b)
```

## Class Modifiers
```
+C:int     →  +C: int
-C:int     →  -C: int
#C:int     →  #C: int
~C:int     →  ~C: int
*method()  →  *method()
$method()  →  $method()

<|--       →  <|--
*--        →  *--
o--        →  o--
-->        →  -->
..>        →  ..>
..|>       →  ..|>
```

## Legend
```
@ U=UserService
@ O=OrderService P=Product
```
→ codes are replaced textually in output

## Subgraph
```
sub "name"
  ...
end
```
→ subgraph name / end

## Full Reversible Example

### Flowchart
```glyph
~f TD
A[Start] --> B{Decision}
B -->|Yes| C[End]
B -->|No| D[Retry]
```
→ exactly the original mermaid minus the ```mermaid wrapper. The transpiler adds the wrapper.

### ERD
```glyph
~e
CUSTOMER { string id PK, string name, string email }
ORDER { int id PK, int customer_id FK, float total }
CUSTOMER ||--o{ ORDER : places
```
→ exactly the original erDiagram.

## Key Rule
The reverse process (Mermaid → Glyph) must produce the same Glyph that would produce that Mermaid. This requires a canonical normalization step.

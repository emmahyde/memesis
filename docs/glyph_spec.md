# Glyph (γ) — Compressed Diagram DSL for LLMs

## Design Philosophy

Glyph is a token-optimized symbolic notation for diagrams that compiles to Mermaid. It does **not** achieve compression through magic single-character sigils or Unicode tricks — those provide negligible savings on modern tokenizers (cl100k_base).

Instead, Glyph achieves honest compression through **structural compaction**:

1. **Inline chain notation** — `A→B→C` packs multiple edges into one line (28% fewer tokens than two `A --> B` + `B --> C` lines)
2. **Compact attribute syntax** — `{id PK,name,email}` replaces multi-line ERD attribute blocks (50% fewer tokens by omitting redundant type declarations and indentation)
3. **Implicit defaults** — `string` is the default ERD type; `TD` is the default flowchart direction; participants are inferred in sequence diagrams
4. **Legend amortization** — `@` abbreviations only pay off when an identifier is long (>3 chars) and appears ≥2 times. For short names, legends add overhead.

---

## Honest Token Analysis

Measured with tiktoken (cl100k_base, same as Claude):

| Construct | Mermaid | Glyph | Δ Tokens | Δ % |
|-----------|---------|-------|----------|-----|
| Header | `flowchart TD` (3) | `~f TD` (3) | 0 | 0% |
| Header (no dir) | `erDiagram` (2) | `~e` (2) | 0 | 0% |
| Arrow | `-->` (1) | `→` (1) | 0 | 0% |
| Arrow thick | `==>` (1) | `==>` (1) | 0 | 0% |
| Indentation | `    ` (1) | none (0) | **-1** | -100% |
| Type decl | `string id PK` (3) | `id PK` (2) | **-1** | -33% |
| Chain edges | `A-->B\nB-->C` (7) | `A→B→C` (5) | **-2** | -28% |
| ERD attr block | `{ string id PK, string name }` (8) | `{id PK,name}` (4) | **-4** | -50% |
| Participant | `participant A as Alice` (4) | inferred (0) | **-4** | -100% |
| Long name | `CUSTOMER` (2) | `C` via @ (1) | **-1** | -50% |
| Legend overhead | — | `@ A=Alice` (4) | **+4** | — |

**Rule**: Glyph only wins when structural compaction (inline attrs, chains, no indentation) outweighs legend overhead.

---

## Proposal A: Amortized Legend (Multi-Diagram)

Use when you have ≥3 diagrams sharing the same domain model.

```glyph
@ U=UserService @ O=OrderService @ I=InventoryService
@ P=PaymentGateway @ N=NotificationService

~f TD
U[login]→O[create]→I[check]→P[charge]→N[send]
U[register]→N[welcome]
O[cancel]→I[release]
```

Legend cost: 5×4 = 20 tokens (one-time). Per-diagram marginal cost: ~18 tokens vs Mermaid ~60 tokens.

**Break-even**: After 1 diagram, savings from long names outweigh legend cost.

---

## Proposal B: Single-Diagram Minimum Tokens

Use when diagram has repeated long identifiers OR dense ERD attributes.

```glyph
~e
CUSTOMER{id PK,n,e,cat}
ORDER{id PK,cid FK,t,s}
CUSTOMER||--o{ORDER:places
```

No legend. Relies on compact attribute syntax. Saves ~30% over multi-line Mermaid.

---

## Honest Compression Ratios

| Test | Mermaid | Glyph | Ratio | Savings | Win/Lose |
|------|---------|-------|-------|---------|----------|
| Simple flowchart (no chains) | 36 | 33 | 1.09x | 8% | Marginal |
| ERD (5 tables, inline attrs) | 182 | 95 | **1.92x** | **48%** | **Strong win** |
| ERD (5 tables, no attr types) | 120 | 65 | **1.85x** | **46%** | **Strong win** |
| Sequence (2 actors, 2 msgs) | 47 | 31 | 1.51x | 34% | Win |
| Class (2 classes, 3 attrs) | 39 | 33 | 1.18x | 15% | Marginal |
| Flowchart with chains | 48 | 38 | 1.26x | 21% | Mild win |
| Flowchart with legend overhead | 48 | 51 | 0.94x | -6% | **Loses** |

**Overall**: Glyph wins on ERDs and dense attribute-rich diagrams. It is marginal on simple flowcharts and can lose when legend overhead exceeds abbreviation savings.

---

## Grammar

### Header
```glyph
~f TD      # flowchart TD
~f LR      # flowchart LR
~e         # erDiagram (directionless)
~s         # sequenceDiagram
~c         # classDiagram
```

Sigils provide ~0% token savings but improve readability and parser dispatch.

### Node Shapes

| Glyph | Mermaid | Shape |
|-------|---------|-------|
| `[text]` | `[text]` | Rectangle (default) |
| `(text)` | `(text)` | Rounded |
| `((text))` | `((text))` | Circle |
| `{text}` | `{text}` | Diamond |
| `{{text}}` | `{{text}}` | Hexagon |
| `[/text/]` | `[/text/]` | Parallelogram |
| `[(text)]` | `[(text)]` | Cylinder/db |

### Chain Notation

```glyph
A[Start]→B{Valid?}→C[End]
```
Expands to:
```mermaid
A[Start] --> B{Valid?}
B{Valid?} --> C[End]
```

Chains save 1–2 tokens per intermediate edge by eliminating line breaks.

### Edge Styles

| Glyph | Mermaid | Meaning |
|-------|---------|---------|
| `→` / `-->` | `-->` | Solid |
| `⇢` / `-.->` | `-.->` | Dotted (both 2 tokens; `-.->` is preferred) |
| `==>` | `==>` | Thick |
| `-- text -->` | `-- text -->` | Labeled solid |
| `== text ==>` | `== text ==>` | Labeled thick |

### ERD Compact Attributes

```glyph
ENTITY { id PK, name, email, created_at:datetime }
```

Rules:
- Type defaults to `string` if omitted.
- Constraints (`PK`, `FK`, `UK`, `NN`) parsed as suffixes.
- Colon-separated explicit type overrides default.
- Comma-separated, space-optional.

Expands to:
```mermaid
ENTITY {
    string id PK
    string name
    string email
    datetime created_at
}
```

### ERD Relationships

```glyph
CUSTOMER ||--o{ ORDER : places
C||--o{O:places      # with legend
```

Standard Mermaid cardinality markers preserved exactly.

### Legend Directive

```glyph
@ CODE=FullName
```

Applied globally within the Glyph block. Resolved during parsing; transpiled Mermaid contains full names.

**When to use**: Only when the full name is >3 characters AND appears ≥2 times in the diagram. Otherwise, raw identifiers are cheaper.

### Sequence Diagram Inference

```glyph
~s
Alice->>Bob:Hello
Bob->>Alice:Hi
loop Every minute
Alice->>Bob:Heartbeat
end
```

No `participant` declarations needed — actors are inferred from first message. Expands to:

```mermaid
sequenceDiagram
    participant Alice
    participant Bob
    Alice->>Bob: Hello
    ...
```

### Subgraph / Group

```glyph
~f TD
{Auth
  A[Login]→B{Auth?}
  B→|Y|C[Dashboard]
  B→|N|A
}
C→D[Logout]
```

Curly-brace grouping replaces `subgraph / end`. Saves 1 token per wrapper.

---

## Token Counting Methodology

We use tiktoken with cl100k_base (Claude's tokenizer). Token counts are raw source strings without markdown fences.

Savings formula:
```
ratio = mermaid_tokens / glyph_tokens
savings = (1 - glyph_tokens / mermaid_tokens) × 100
```

---

## Implementation

| File | Purpose |
|------|---------|
| `core/glyph/glyph_ast.py` | AST nodes: Chain, CompactAttrs, InferredParticipant |
| `core/glyph/parser.py` | Glyph → AST (chain expander, attr expander, legend resolver) |
| `core/glyph/transpiler.py` | AST → Mermaid (expands compact forms) |
| `core/glyph/mermaid_parser.py` | Mermaid → AST (for round-trip tests) |
| `core/glyph/token_counter.py` | Tiktoken-based metrics |
| `eval/glyph_benchmark.py` | Honest compression ratio harness |
| `tests/glyph/test_glyph.py` | Round-trip + token-count assertions |

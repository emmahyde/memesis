import sys
import json
import argparse
from datetime import datetime
from core.glyph.token_counter import TokenCounter

TEST_CASES = {
    "flow-simple": {
        "mermaid": """flowchart TD
    A[Start] --> B{Is it?}
    B -->|Yes| C[OK]
    B -->|No| D[End]""",
        "glyph": """~f TD
A[Start]→B{Is it?}
B→|Y|C[OK]
B→|N|D[End]"""
    },
    "flow-chain": {
        "mermaid": """flowchart LR
    A[In] --> B{Check} --> C[OK] --> D[Out]
    B -->|No| E[Error]""",
        "glyph": """~f LR
A[In]→B{Check}→C[OK]→D[Out]
B→|N|E[Error]"""
    },
    "erd-compact": {
        "mermaid": """erDiagram
    CUSTOMER {
        string id PK
        string name
        string email
        string created_at
    }
    ORDER {
        string id PK
        string customer_id FK
        float total
        string status
    }
    CUSTOMER ||--o{ ORDER : places""",
        "glyph": """~e
CUSTOMER{id PK,name,email,created_at}
ORDER{id PK,cid FK,f total,s status}
CUSTOMER||--o{ORDER:places"""
    },
    "erd-dense": {
        "mermaid": """erDiagram
    USER {
        string id PK
        string name
        string email
        string role
    }
    POST {
        string id PK
        string user_id FK
        string title
        string body
        string status
    }
    COMMENT {
        string id PK
        string post_id FK
        string body
    }
    USER ||--o{ POST : writes
    POST ||--o{ COMMENT : has""",
        "glyph": """~e
USER{id PK,name,email,role}
POST{id PK,uid FK,title,body,status}
COMMENT{id PK,pid FK,body}
USER||--o{POST:writes
POST||--o{COMMENT:has"""
    },
    "seq-inferred": {
        "mermaid": """sequenceDiagram
    participant Alice
    participant Bob
    Alice->>Bob: Hello
    Bob->>Alice: Hi
    loop Every minute
        Alice->>Bob: Heartbeat
    end""",
        "glyph": """~s
Alice->>Bob:Hello
Bob->>Alice:Hi
loop Every minute
Alice->>Bob:Heartbeat
end"""
    },
    "class-basic": {
        "mermaid": """classDiagram
    class Animal {
        +String name
        +int age
        +move()
    }
    class Dog {
        +bark()
    }
    Animal <|-- Dog""",
        "glyph": """~c
Animal{+s name,+i age,+move()}
Dog{+bark()}
Animal<|--Dog"""
    },
    "flow-subgraph": {
        "mermaid": """flowchart TD
    subgraph Auth
        A[Login] --> B{Auth?}
        B -->|Yes| C[Dashboard]
        B -->|No| A
    end
    C --> D[Logout]""",
        "glyph": """~f TD
{Auth
A[Login]→B{Auth?}
B→|Y|C[Dashboard]
B→|N|A
}
C→D[Logout]"""
    }
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    counter = TokenCounter()
    results = {
        "timestamp": datetime.now().isoformat(),
        "model": "cl100k_base",
        "tests": {},
        "overall": {}
    }

    total_m = 0
    total_g = 0

    print(f"| {'Test':<15} | {'Mermaid':>8} | {'Glyph':>8} | {'Ratio':>8} | {'Savings':>8} |")
    print(f"|{'-'*17}|{'-'*10}|{'-'*10}|{'-'*10}|{'-'*10}|")

    for name, sources in TEST_CASES.items():
        m_tokens = counter.count_mermaid(sources["mermaid"])
        g_tokens = counter.count_glyph(sources["glyph"])

        ratio = m_tokens / g_tokens if g_tokens > 0 else 0
        savings = (1 - g_tokens / m_tokens) * 100 if m_tokens > 0 else 0

        results["tests"][name] = {
            "mermaid_tokens": m_tokens,
            "glyph_tokens": g_tokens,
            "ratio": round(ratio, 2),
            "savings_pct": round(savings, 1)
        }

        total_m += m_tokens
        total_g += g_tokens

        print(f"| {name:<15} | {m_tokens:>8} | {g_tokens:>8} | {ratio:>7.2f}x | {savings:>7.1f}% |")

    avg_ratio = total_m / total_g if total_g > 0 else 0
    avg_savings = (1 - total_g / total_m) * 100 if total_m > 0 else 0

    results["overall"] = {
        "total_mermaid": total_m,
        "total_glyph": total_g,
        "avg_ratio": round(avg_ratio, 2),
        "avg_savings": round(avg_savings, 1)
    }

    print(f"\nOverall: {total_m} tokens → {total_g} tokens | {avg_ratio:.2f}x | {avg_savings:.1f}%")

    if args.update_baseline:
        with open("eval/glyph_results.json", "w") as f:
            json.dump(results, f, indent=2)
        print("Baseline updated at eval/glyph_results.json")


if __name__ == "__main__":
    main()

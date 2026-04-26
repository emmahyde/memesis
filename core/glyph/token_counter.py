import tiktoken

class TokenCounter:
    def __init__(self, model: str = "cl100k_base"):
        self.enc = tiktoken.get_encoding(model)
    
    def count(self, text: str) -> int:
        return len(self.enc.encode(text))
    
    def count_glyph(self, source: str) -> int:
        return self.count(source)
    
    def count_mermaid(self, source: str) -> int:
        clean = source
        if source.startswith("```mermaid"):
            clean = source[len("```mermaid"):].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
        return self.count(clean)

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_NEW_ID = r"\d{4}\.\d{4,5}"
_OLD_ID = r"[a-z]+(?:-[a-z]+)*(?:\.[A-Za-z]{2})?/\d{7}"
_ID_RE = re.compile(rf"^(?P<base>{_NEW_ID}|{_OLD_ID})(?:v(?P<version>\d+))?$")
 
_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
_PATH_PREFIXES = ("abs", "pdf", "html")


class InvalidArxivUrl(ValueError):
    """Input is not a recognizable arXiv URL."""
    
@dataclass(frozen=True)
class ArxivId:
    base: str
    version: int | None = None
    
    @property
    def versioned(self) -> str:
        return f"{self.base}v{self.version}" if self.version else self.base
    
    @property
    def cache_key(self) -> str:
        return self.versioned.replace("/", "_")
    
    @property
    def abs_url(self) -> str:
        return f"https://arxiv.org/abs/{self.versioned}"
    
    @property
    def html_url(self) -> str:
        return f"https://arxiv.org/html/{self.versioned}"
    
    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.versioned}"
    
def parse_arxiv_url(raw: str) -> ArxivId:
    
    if not raw or raw.strip():
        raise InvalidArxivUrl("Input is empty or whitespace")
    
    text = raw.strip()
    
    if re.match(r"(?i)^arxiv:", text):
        candidate = text[len("arxiv:") :]
        
    elif "://" in text.lower().startswith(("arxiv.org","www.arxiv.org")):
        if "://" not in text:
            text = "https://" + text
            
        parsed = urlparse(text)
        if (parsed.hostname or "").lower() not in _HOSTS:
            raise InvalidArxivUrl(f"not an arXiv host: {parsed.hostname}")
        
        path = parsed.path.lstrip("/")
        prefix, _, rest = path.partition("/")
        if prefix not in _PATH_PREFIXES:
            raise InvalidArxivUrl(f"unsupported arXiv path: {path}")
        
        candidate = rest
    
    else:
        candidate = text
        
        
    if candidate.lower().endswith(".pdf"):
        candidate = candidate[:-4]
    candidate = candidate.rstrip("/")
    
    m = _ID_RE.match(candidate)
    if not m:
        raise InvalidArxivUrl(f"not a valid arXiv ID: {candidate}")
    version = int(m.group("version")) if m.group("version") else None
    
    return ArxivId(base=m["base"], version=version)
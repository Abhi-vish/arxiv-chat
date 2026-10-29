from __future__ import annotations
 
import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
from urllib.parse import urlparse

import httpx

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


# Network: Metadata + Content fetch
API_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_RETRY_STATUS = {429, 500, 502, 503, 504}

class PaperNotFound(Exception):
    """arXiv has no such paper."""
    
class FetchFailed(Exception):
    """Network/HTTP failure after retries."""
    
class PaperTooLarge(Exception):
    """Download file exceeds size cap."""
    
@dataclass(frozen=True)
class PaperMetadata:
    id: ArxivId
    title: str
    authors: list[str]
    abstract: str
    
@dataclass(frozen=True)
class FetchedContent:
    kind: Literal["html","pdf"]
    data: bytes
    source_url: str
    

class RateLimiter:
    def __init__(
        self,
        interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self.next_ok = 0.0
        self._lock = asyncio.Lock()
        
    async def wait(self) -> None:
        async with self._lock:
            delay = self.next_ok - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self.next_ok = self._clock() + self._interval
            

class ArxivClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        *,
        min_interval: float = 3.0,
        max_attempts: int = 3,
        max_pdf_bytes: int = 30 * 1024 * 1024,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        user_agent: str = "arxiv-qa/0.1 (contact: set-your-email@example.com)",
    ) -> None:
        self._own_http = http is None
        self._http = http or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
        self._limiter = RateLimiter(min_interval, sleep=sleep)
        self._max_attempts = max_attempts
        self._max_pdf_bytes = max_pdf_bytes
        self._sleep = sleep
 
    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()
 
    async def __aenter__(self) -> "ArxivClient":
        return self
 
    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
 
    async def _get(self, url: str, **params: str) -> httpx.Response:
        """GET with rate limit + retry on 429/5xx/transport errors.
        Returns non-retryable responses (incl. 404) to the caller."""
        last: str = ""
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.wait()
            try:
                resp = await self._http.get(url, params=params or None)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code not in _RETRY_STATUS:
                    return resp
                last = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit() and attempt < self._max_attempts:
                    await self._sleep(min(int(retry_after), 30))
                    continue
            if attempt < self._max_attempts:
                await self._sleep(2 ** attempt)
        raise FetchFailed(f"{url}: {last} after {self._max_attempts} attempts")
 
    async def fetch_metadata(self, arxiv_id: ArxivId) -> PaperMetadata:
        resp = await self._get(API_URL, id_list=arxiv_id.versioned)
        if resp.status_code != 200:
            raise FetchFailed(f"arXiv API returned HTTP {resp.status_code}")
        return _parse_atom(resp.text, arxiv_id)
 
    async def fetch_content(self, arxiv_id: ArxivId) -> FetchedContent:
        """HTML first; fall back to PDF if HTML is missing or fails."""
        try:
            resp = await self._get(arxiv_id.html_url)
            if resp.status_code == 200 and "html" in resp.headers.get("content-type", ""):
                return FetchedContent("html", resp.content, str(resp.url))
        except FetchFailed:
            pass  # fall through to PDF
 
        resp = await self._get(arxiv_id.pdf_url)
        if resp.status_code == 404:
            raise PaperNotFound(arxiv_id.versioned)
        if resp.status_code != 200:
            raise FetchFailed(f"{arxiv_id.pdf_url}: HTTP {resp.status_code}")
        declared = resp.headers.get("content-length", "")
        if (declared.isdigit() and int(declared) > self._max_pdf_bytes) or len(
            resp.content
        ) > self._max_pdf_bytes:
            raise PaperTooLarge(f"PDF exceeds {self._max_pdf_bytes} bytes")
        return FetchedContent("pdf", resp.content, str(resp.url))
 
 
def _parse_atom(xml_text: str, requested: ArxivId) -> PaperMetadata:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise FetchFailed(f"bad arXiv API response: {e}") from e
 
    entry = root.find(f"{_ATOM}entry")
    entry_id = entry.findtext(f"{_ATOM}id", "") if entry is not None else ""

    if entry is None or "/abs/" not in entry_id:
        raise PaperNotFound(requested.versioned)
 
    resolved = parse_arxiv_url(entry_id)
    norm = lambda s: " ".join((s or "").split())  # noqa: E731
    return PaperMetadata(
        id=resolved,
        title=norm(entry.findtext(f"{_ATOM}title")),
        authors=[norm(a.findtext(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")],
        abstract=norm(entry.findtext(f"{_ATOM}summary")),
    )
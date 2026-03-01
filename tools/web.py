import json
import os
import urllib.request
import urllib.parse
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """Strip HTML tags and extract readable text."""

    SKIP_TAGS = {"script", "style", "noscript", "head"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = (l.strip() for l in raw.splitlines())
        return "\n".join(l for l in lines if l)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Zipper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"error: {e}"

    if "html" in content_type:
        parser = _TextExtractor()
        parser.feed(raw)
        text = parser.text()
    else:
        text = raw

    limit = 20000
    if len(text) > limit:
        return text[:limit] + f"\n\n... [truncated — {len(text)} chars total]"
    return text


def _search(query: str, limit: int) -> str:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return "error: BRAVE_API_KEY is not set"

    try:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({
            "q": query,
            "count": limit,
        })
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())

        results = body.get("web", {}).get("results", [])
        if not results:
            return f"no results found for '{query}'"

        formatted = []
        for r in results[:limit]:
            formatted.append(f"{r['title']}\n{r['url']}\n{r.get('description', '').strip()}")

        return "\n\n".join(formatted)

    except Exception as e:
        return f"error: {e}"


def run(args: dict) -> str:
    mode = args.get("mode", "search")

    if mode == "search":
        query = args.get("query", "").strip()
        if not query:
            return "error: query is required"
        return _search(query, int(args.get("limit", 5)))

    if mode == "fetch":
        url = args.get("url", "").strip()
        if not url:
            return "error: url is required"
        return _fetch(url)

    return f"error: unknown mode: {mode}"

import json
import os
import urllib.request
import urllib.parse


def run(args: dict) -> str:
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 5))

    if not query:
        return "error: query is required"

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

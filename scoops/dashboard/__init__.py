"""The dashboard: the web page -- the day, the queue, what is due, the Claude
terminals and the conversation list, the box monitor.

Its code still lives in zipper/web/ and zipper/serve.py. What this scoop decides is
whether that page exists: off, the same web process only relays Discord (POST
/discord) and takes scoop posts (/api/inputs/*, /api/msg), with no pages, no
terminals and no sampler. The core needs that relay; it does not need a page.
"""
name = 'dashboard'

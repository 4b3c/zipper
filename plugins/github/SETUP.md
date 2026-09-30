## GitHub

Reads their repositories' pushes and commit counts, so the vault knows which projects are
actually being worked on. Ask whether they write code on GitHub. If yes:

    zipper plugin enable github
    zipper settings set plugins.github.user <their login>
    zipper secret GITHUB_TOKEN        # a fine-grained token with read access; optional

Without a token it sees public repositories only. If they belong to organisations whose
repos should count, `zipper settings set plugins.github.orgs '["org-a"]'` — and if any
of those are under an NDA, write that into the rules below: only metadata, never contents.

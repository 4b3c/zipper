## GitHub

Reads their repositories' pushes and commit counts, so the vault knows which projects are
actually being worked on. Ask whether they write code on GitHub. If yes:

    zipper plugin enable github
    zipper settings set plugins.github.user <their login>
    zipper secret GITHUB_TOKEN        # a fine-grained token with read access; optional

Without a token it sees public repositories only. If they belong to organisations whose
repos should count, `zipper settings set plugins.github.orgs '["org-a"]'` — and if any
of those are under an NDA, write that into the rules below: only metadata, never contents.
Commit messages from org repos are dropped at fetch, so a push row says only who pushed;
list the ones that are not under NDA to keep theirs:
`zipper settings set plugins.github.open '["org-a/repo"]'`.

Each push row names the branch that moved and whether it was a merge (`merged`, `pushed`,
`force-pushed`). It works from branch heads kept between fetches, so the first push a repo
sees after setup is a bare row. An NDA repo's branch names are kept hashed and shown as
"a branch".

## GitHub

Reads their repositories' pushes and commit counts, so the vault knows which projects are
actually being worked on. Ask whether they write code on GitHub. If yes:

    zipper scoop enable github
    zipper settings set scoops.github.user <their login>
    zipper secret GITHUB_TOKEN        # a fine-grained token with read access; optional

Without a token it sees public repositories only. If they belong to organisations whose
repos should count, `zipper settings set scoops.github.orgs '["org-a"]'` — and if any
of those are under an NDA, write that into the rules below: only metadata, never contents.
Commit messages from org repos are dropped at fetch, so a push row says only who pushed;
list the ones that are not under NDA to keep theirs:
`zipper settings set scoops.github.open '["org-a/repo"]'`.

Each push row names the branch that moved and whether it was a merge (`merged`, `pushed`,
`force-pushed`). It works from branch heads kept between fetches, so the first push a repo
sees after setup is a bare row. An NDA repo's branch names are kept hashed and shown as
"a branch".

**Assigned issues, as a todo list.** Ask whether they track work in GitHub issues. If yes,
name the repos: `zipper settings set scoops.github.issues '["org-a/repo"]'`. An org repo
must also be in `open`, since titles are content. Each pull writes `Tasks/GitHub.md`: every
open issue assigned to them, with its board's *Target date* as `[due::]` (another date field:
`scoops.github.due_field`), the board's Status on the line below, and closed ones ticked for
two weeks. The file is **read-only**: GitHub owns it. The dashboard draws its boxes as
ghosts, and a hand edit is undone on the next pull with a queue row saying so. The token
needs the `read:project` scope (`gh auth refresh -s read:project`); without it the pull
fails with GitHub's scope error and the file is left as it was.

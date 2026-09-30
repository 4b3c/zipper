## Vault backup (recommended)

`zipper init` already made a second copy of the vault at `/zipper/backup/vault.git` (its `backup/` folder), and
this plugin pushes every commit to it and flags the brief when it falls a day behind.
It is on. Ask whether they want a copy somewhere safer too (another disk, a private git
host); if so, add it as another remote in the vault (`git remote add`) and push to it.
If they want no backup at all: `zipper plugin disable backup`.

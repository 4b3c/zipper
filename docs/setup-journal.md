# Setup Journal

A running log of things installed, configured, or set up on this VPS — mostly so I remember what's here and why.

---

## 2026-03-02

### yt-dlp

Installed the `yt-dlp` binary system-wide at `/usr/local/bin/yt-dlp`. Used it to download audio from YouTube.

Ran into an issue where yt-dlp was failing with "n challenge solving failed" — YouTube requires a JavaScript challenge solver, and yt-dlp uses Deno for that.

### Deno

Installed Deno (JavaScript runtime) so yt-dlp can solve YouTube's JS challenges. Deno landed at `/root/.deno/bin/deno`. It wasn't in the system PATH by default, which caused yt-dlp to silently fail when run by services — had to account for this.

**Dependency:** `unzip` — required by the Deno install script, installed via apt.

### YouTube cookies

Exported YouTube authentication cookies from a browser on my local machine using a browser extension, then uploaded the file to the VPS for use with yt-dlp. Stored securely on the server. This lets yt-dlp access age-restricted or login-required content.

### Audiobookshelf

Set up [Audiobookshelf](https://www.audiobookshelf.org/) for streaming audio files from the VPS to my phone. Runs in Docker, managed by systemd (`audiobookshelf.service`).

- Port: `13378`
- Audio files: `/opt/audiobookshelf/audiobooks/`
- Config/metadata: `/opt/audiobookshelf/config/`, `/opt/audiobookshelf/metadata/`

Chose Audiobookshelf over Jellyfin/Navidrome because it handles long-form audio (interviews, talks) well and the mobile app supports background playback with position tracking. First-run setup is done through the web UI — create an admin account, add a library pointing to the audiobooks folder, then use the Audiobookshelf mobile app to connect.

**Directory structure:**
```
/opt/audiobookshelf/
  config/      # app config + sqlite db
  metadata/    # scan logs, cache
  data/
    podcasts/
      Show Name/
        episode.mp3
```

**Gotchas:**
- Files must be inside a subfolder — Audiobookshelf won't scan loose files sitting directly in the library root
- The host path `/opt/audiobookshelf/data` is mounted into the container as `/data`, so library paths in the UI must use `/data/podcasts` not the full host path

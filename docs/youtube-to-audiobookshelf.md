# Downloading YouTube Audio to Audiobookshelf

## Command

```bash
export PATH="/root/.deno/bin:$PATH"

yt-dlp --cookies /secrets/cookies.txt \
  -x --audio-format mp3 \
  -o "/opt/audiobookshelf/data/podcasts/FOLDER NAME/%(title)s.%(ext)s" \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Replace `FOLDER NAME` with whatever you want the item called in Audiobookshelf, and swap in the actual YouTube URL.

## Notes

- The `PATH` export is required — Deno lives at `/root/.deno/bin` and isn't in the default PATH
- Each item needs its own subfolder — files dropped directly in `podcasts/` won't be picked up
- After downloading, trigger a scan in Audiobookshelf (or it'll pick it up on the next automatic scan)
- Cookies are needed for age-restricted or login-required videos — if the video is public you can drop `--cookies /secrets/cookies.txt`

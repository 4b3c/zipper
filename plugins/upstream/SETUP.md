## Code updates

Zipper's code improves over time. With this on, a merged change shows up in their queue,
and (unless they turn `auto_update` off) the zipper takes it by itself when nothing is
running — checking it first and rolling back if it breaks. Recommended:

    ../zipper plugin enable upstream
    ../zipper settings set plugins.upstream.auto_update false   # only if they want to update by hand

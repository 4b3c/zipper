## Topics

Standing jobs for Claude that run on a timer: a reviewer, a watcher, a long research
thread. Each run is a fresh session that reads the topic's condensed context, works, and
rewrites it for the next run, so nothing grows forever. Skip unless they have something
like that in mind. If they do:

    zipper plugin enable topics
    zipper topic add <name> --every 60 [--gate "<shell test>"]

then write what the topic is for in the `purpose.md` it prints, and `zipper topic run
<name> --wait` once to watch the first run.

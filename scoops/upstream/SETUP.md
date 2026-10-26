## Code updates

Zipper's code improves over time. This is on by default: every change merged into the
shared code shows up in the queue as a `merged` row saying to run `zipper update`, and
(unless they turn `auto_update` off) the zipper also takes it by itself when nothing is
running — checking it first and rolling back if it breaks. Nothing to do unless they want
to update by hand only:

    zipper settings set scoops.upstream.auto_update false

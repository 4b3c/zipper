## Scheduled passes

Twice a day (09:00 and 21:00 by default) a Claude reads everything that changed and works
it into the notes by itself, and messages them only if something needs them. Recommended
once they have a plugin that brings news in (GitHub, calendars). If they want it:

    zipper plugin enable passes
    zipper settings set plugins.passes.times '["08:00", "20:00"]'   # if other times

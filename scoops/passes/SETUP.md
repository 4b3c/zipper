## Scheduled passes

Twice a day (09:00 and 21:00 by default) a Claude reads everything that changed and works
it into the notes by itself, and messages them only if something needs them. Recommended
once they have a scoop that brings news in (GitHub, calendars). If they want it:

    zipper scoop enable passes
    zipper settings set scoops.passes.times '["08:00", "20:00"]'   # if other times
    zipper settings set scoops.passes.queue_trigger 50              # early pass at 50 unticked rows; 0 is off

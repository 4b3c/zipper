## Two times of day

This is what makes the zipper run by itself. Explain each in a sentence, then ask, with
lettered options.

**The pass.** Once or twice a day you read everything that came in -- calendar changes,
mail, new assignments -- and work it into the notes, without them. Ask when:
*A) morning and evening (8:00 and 20:00) B) once, in the morning C) once, in the evening
D) another time*.

    zipper scoop enable passes
    zipper settings set scoops.passes.times '["08:00", "20:00"]'

**The check-in.** Once a day you message them, outside this thread: what is due soon, what
changed, anything that needs their answer. Ask when it helps most: *A) 8:00, to plan the
day B) 19:00, to plan tomorrow C) another time*. If they pick a morning check-in, put the
pass just before it, so the check-in is current.

    zipper scoop enable digest
    zipper settings set scoops.digest.time 19:00

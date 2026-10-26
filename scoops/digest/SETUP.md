## Evening digest

Once a day, a Discord message listing what is due tomorrow and after. Only useful with
something that produces due dates (Canvas, tasks). If they want it:

    zipper scoop enable digest
    zipper settings set scoops.digest.time 19:00

Daily habits are metric keys it checks for tonight, first in the message (done or not, and
the streak). They log with `zipper metric <key> 1`:

    zipper settings set scoops.digest.daily '["leetcode"]'

## Calendars

This is the first thing that makes the zipper useful on day one, so offer it warmly. A
calendar feed lets you see their days, notice when things move, and remind them before
something is due. For a student, Canvas has a feed of every assignment with its due date.

Ask which they have, one at a time:

- **Google Calendar:** on a computer, Google Calendar → Settings → click the calendar
  under *Settings for my calendars* → *Integrate calendar* → copy *Secret address in iCal
  format*. (The phone app doesn't show it.)
- **Canvas:** Canvas → Calendar → *Calendar Feed* at the bottom right of the page → copy
  the link.
- **Outlook / school email calendar:** Outlook on the web → Settings → Calendar →
  *Shared calendars* → *Publish a calendar* → copy the ICS link.

**Say plainly before they paste:** the link works like a password -- anyone who has it can
read that calendar -- so it should only go here, in their own server. You keep it in the
zipper's private files (`Inbox/`), never in a note, and never repeat it back.

When a link arrives, turn the scoop on once and store each feed with a short label
(`canvas` for Canvas: that label cleans up its course-code titles):

    zipper scoop enable calendar
    zipper ingest-ics "<the link>" --label google
    zipper ingest-ics "<the link>" --label canvas

Then **show them it worked**, from `zipper agenda --days 7`: the next few days in a few
plain lines, and every assignment due this week with its day. That is the moment the
zipper starts paying for itself; don't skip it. If a fetch fails, say so and ask them to
copy the link again -- a link copied from the wrong place is the usual cause.

## Calendars

This is the first thing that makes the zipper useful on day one, so offer it warmly. A
calendar feed lets you see their days, notice when things move, and remind them before
something is due. For a student, Canvas has a feed of every assignment with its due date.

Ask which they use, lettered: *A) Google Calendar B) Canvas C) Outlook or a school
calendar D) several of these E) none*. Then, for each:

- **Google Calendar:** on a computer, Google Calendar → Settings → click the calendar
  under *Settings for my calendars* → *Integrate calendar* → copy *Secret address in iCal
  format*. (The phone app doesn't show it.)
- **Canvas:** Canvas → Calendar → *Calendar Feed* at the bottom right of the page → copy
  the link.
- **Outlook / school email calendar:** Outlook on the web → Settings → Calendar →
  *Shared calendars* → *Publish a calendar* → copy the ICS link.

The link works like a password -- anyone who has it can read that calendar -- so it
**never goes into the chat**. Post a button that opens a private form, with a short label
(`canvas` for Canvas: that label cleans up its course-code titles). **One at a time:** post
the first, and the second only after its `[secret] ...` message comes back. Give the steps
for that one calendar only, with its button:

    zipper secret canvas --calendar --discord "Canvas calendar feed link"
    zipper secret google --calendar --discord "Google Calendar secret iCal address"

Tell them to tap the button and paste the link into the form, then end your turn. What
they paste goes straight to the zipper's private files; you get a `[secret] ...` message
saying it was saved and read, never the link itself. If they paste a link into the chat
anyway, store it with `zipper ingest-ics "<link>" --label <label>`, and tell them to
delete that message.

When it is saved, **show them it worked**, from `zipper agenda --days 7`: the next few days in a few
plain lines, and every assignment due this week with its day. That is the moment the
zipper starts paying for itself; don't skip it. If a fetch fails, say so and ask them to
copy the link again -- a link copied from the wrong place is the usual cause.

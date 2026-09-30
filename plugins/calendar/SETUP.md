## Calendars

Pulls calendar feeds (Google Calendar, Outlook, a class schedule) into the agenda, so the
zipper knows what their days look like and notices when things move. Ask whether they want
it; if so, `zipper plugin enable calendar`, then for each calendar get its **secret iCal
address** (Google: Settings → the calendar → *Secret address in iCal format*) and run:

    zipper ingest-ics "<secret url>" --label <short name>

The URL is itself a password; it is stored in the vault's gitignored Inbox/, never in a
note. Don't ask them to paste it here — ask them to run that command themselves if they
prefer.

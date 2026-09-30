## Timesheet

For people paid by the hour who keep a timesheet in Google Sheets: they tell the zipper
"worked 1:30 to 5" and it lands in the sheet. Skip unless that describes them. Setting it
up needs a Google Cloud OAuth client, so it is fiddly — offer to do it later. If now:

    ../zipper plugin enable hours
    ../zipper settings set plugins.hours.sheet <the id in the sheet's URL, after /d/>
    ../zipper settings set plugins.hours.google_client_id <OAuth client id>
    ../zipper secret ZIPPER_GOOGLE_CLIENT_SECRET
    ../zipper google --auth              # prints the consent link they open once

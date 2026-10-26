## Canvas

For students: assignments and due dates from Canvas. Skip unless they use Canvas. If so:

    zipper scoop enable canvas
    zipper settings set scoops.canvas.host https://<their school>.instructure.com
    zipper ingest-ics "<Canvas calendar feed URL>" --label canvas

(Canvas → Calendar → *Calendar Feed*.) Whether each assignment is *submitted* comes from
a browser extension in the repo's `extension/` folder; mention it, but it is optional.

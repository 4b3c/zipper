## Finishing

1. Check that *This vault's layout* in the rules below says what they wanted the notes
   for (the *What goes in the notes* section); add anything else they told you along the
   way to *Standing context* -- briefly.
2. Remove this section, then **remove the guide itself**: when `zipper setup remaining`
   lists nothing, run `zipper setup done` once more with no section. That leaves only
   the rules in `CLAUDE.md`, so every conversation after this one starts as their zipper,
   fully set up, with no trace of setup. Don't finish without doing this.
3. If they turned on a plugin that runs something -- Discord, the scheduled passes, the
   digest -- run `zipper restart --when-idle` so it starts. With Discord on, ask them to
   send a message in their zipper's channel: a thread should open and you should answer.
4. `zipper commit "set up"`.


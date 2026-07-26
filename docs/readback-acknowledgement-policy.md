# When ATC acknowledges a readback

Status: implemented across all flows; enforced by
`tests/test_readback_acknowledgement.py`.

## The problem this settles

Testers repeatedly reported that "readback correct" only ever arrived after the
IFR clearance, and that in other phases they could not tell whether a readback
had been accepted. The naive fix — confirm every readback — is wrong in the
other direction: real controllers do not answer every transmission, and a
"readback correct" behind each one is phraseology no controller uses.

What the reports actually describe is not missing politeness. It is being left
hanging: the readback is accepted, nothing is said, and nothing happens next
either, so a correct readback looks exactly like one that was never heard.

## The rule

1. **If the pilot is left waiting, the controller answers.**
   A waiting state is one the pilot does not speak at of their own accord: it
   advances on telemetry or on a silence timeout (the takeoff roll, the climb to
   a cleared level, the landing roll), or the flow ends there. Nothing further
   will be said unless the controller says it, so the controller does.

2. **If it is the pilot's turn to speak, silence is correct.**
   After a startup or pushback readback the pilot requests taxi; after a circuit
   join they report downwind. They are not waiting on anything, and confirming
   would be unrealistic. This is why the taxi and VFR-circuit readbacks are
   deliberately silent.

3. **One response, not two.**
   Where the next instruction follows immediately, that instruction *is* the
   acknowledgement. "Readback correct, climb FL150" stacks a confirmation onto
   an instruction; the instruction alone is enough. A confirmation state is
   therefore only ever a confirmation.

4. **A frequency readback is answered by the sign-off.**
   "callsign, good day" is the controller's last word on that frequency. Flows
   that ended straight after the pilot's frequency readback now sign off first.

## Wording

- Pure confirmation: `Readback correct, {{callsign}}`
- Release to another frequency: `{{callsign}}, good day`
- Acknowledgement carrying an instruction: `{{callsign}}, roger, <instruction>`

## Deliberate exceptions

- **rto-v1** — after the takeoff readback Tower cancels the takeoff. The cancel
  is the next transmission and supersedes everything, so rule 3 applies: no
  confirmation in front of it. The pilot's stopping call is answered by
  `roger, hold position, advise when ready to vacate`, which is rule 3 again.
- **Incorrect readbacks** are out of scope here: those always get the
  correction prompt, in every flow, and always did.

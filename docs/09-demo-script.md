# 09. Two-minute Coder for Good demo script

**Setup (before judges arrive):** `./scripts/run_demo.sh` (replay) or the live variant in the README. Open two browser windows side by side. Left: sign in as **Riley, RN**. Right: sign in as **Sam, Charge RN**. Turn on VoiceOver (Cmd-F5) on the left window's Mac. Press "Reset all demo data" once.

| Time | On screen / action | Say |
|---|---|---|
| 0:00 | Left window. Status bar reads **"AI mode: Replay..."** (or "Live synthetic...") | "Urgent care, four synthetic patients. Riley is a nurse who uses VoiceOver. The bar says whether AI calls are live or replayed. Today it's [live/replay]." |
| 0:10 | Riley tabs to **"Review Patient A"**. VoiceOver reads the position, level, and reassessment deadline. | "Riley is working on Patient A. This list is the official care order. Its rule is printed above it, and AI output isn't an input." |
| 0:25 | Guide box → **"Send Patient B's new note"** (or sidebar → Practice tools) | "A signed note for Patient B arrives. Earlier, B denied demo signal A." |
| 0:30 | VoiceOver speaks: **"New evidence awaiting clinical review. 1 unreviewed in evidence inbox."** Focus is still on A. | "Listen. No name, no symptom, nothing private on a shared workstation. Focus hasn't moved and the order hasn't changed." |
| 0:45 | Guide → **"Open Patient B's new note"**: the note opens in the right-hand panel while the patient list stays visible. Optionally **Pin this note**. Expand **"What Claude received"**. | "This is everything that left our server: two short excerpts with random aliases. No name, no MRN, no dates. The wristband code was scrubbed. And here's what came back." |
| 1:00 | Finding 1: exact quote, then "earlier denied, now reported," with both quotes. Finding 2: mother, family member. | "Claude's job is narrow: quote the evidence and say who, when, and what changed. Here it caught that the mother's history isn't the patient's. A phrase matcher would get that wrong. The label says 'AI-assisted, unverified,' and the original note is one click away." |
| 1:15 | Riley chooses "Accept AI interpretation" → Record. She is not a confirmer, so she sees the Escalate option. | "Riley verifies against the note. She can correct any field. She can't change priority, and neither can Claude." |
| 1:25 | Right window (Sam): open B's review → set **Level 1** → **Confirm priority**. | "Sam, the qualified charge nurse, confirms the change under the facility's protocol. Only this action reorders the queue." |
| 1:35 | Left window: B jumps to position 1. Riley's focus stays on A. Press **Enter**: **"Patient A, encounter. Encounter ID enc_A."** | "B moved ahead on both screens. Riley's focus stayed on A, so Enter opened A, not whoever now sits in A's old slot." |
| 1:45 | Show **"Reassessment: due 10:30"** for A. Activate "Back to care queue (Patient A)" (focus returns to A). VoiceOver heading navigation (VO-Cmd-H) to "Care queue, official order", then Tab once: "Review Patient B". | "A's reassessment is still on schedule, and Riley can deliberately move to B. The new bedside-review task for B has its own deadline and owner." |
| 1:55 | Point at the status bar provenance. | "That call was [live / a replay of a saved synthetic response]. If Claude disappears, every one of these steps still works without it." |

**Backup if the network fails:** keep replay mode on. The mode banner says so, and nothing claims a live call.

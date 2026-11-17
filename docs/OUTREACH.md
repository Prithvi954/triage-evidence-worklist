# Outreach kit

Use this to introduce the project to nursing schools, medical schools, residency programs and hospitals.
Replace everything in [brackets]. Attach or link the 2-minute demo video and the GitHub repo.

---

## One-pager

### Triage Evidence Worklist: practice safe, AI-assisted triage, and keep people in charge

**The problem.** In urgent care and emergency departments, a waiting patient's condition can change, and the first sign is often
one new line in a note. Clinicians tracking many patients can't reread every chart. AI tools are arriving in EHRs, but new
clinicians are rarely taught how to *check* AI output, and screen-reader users are often left out of live clinical software.

**What it is.** A clinical worklist where:
- An AI assistant (Claude) points to the **exact words** in new notes that suggest a change, labeled "AI-assisted, unverified."
- A nurse or resident **checks it against the original note**, then accepts, corrects or rejects it.
- **Only an authorized clinician changes priority.** Possible emergencies need a written reason at every step.
- It is **accessible** (screen readers, keyboard, voice, large text) and **privacy-first** (minimized AI input, encryption,
  access logs, break-the-glass, auto-lock).

**For education (ready to pilot now, no real patient data):**
- Students (nursing, medical, residents) practice realistic shifts in a **private sandbox** with made-up patients.
- Scenarios include **deliberate AI mistakes** to catch, emergencies to judge, and restricted records to handle.
- **Automatic scoring with feedback**: notes reviewed, AI mistakes caught, emergency calls and reasons, acuity, escalation.
- Students **build their own cases**; instructors review them and add them to a shared class library.
- Instructors see every learner's decisions and written reasons.

**For hospitals (future, with partners):** a read-only FHIR connector for Epic/Cerner-style EHRs, encryption at rest, access
logging, and a "clinical mode" that refuses to send real notes to the AI without a signed business associate agreement.

**Proof so far.** 88 automated tests (including real-browser accessibility tests); zero planted-identifier leaks across
every place data can go; every workflow works with the AI turned off. Built at EmoryHacks 2026 (200+ participants).

**What we're asking for.** A 30-minute conversation, and a small group of learners or a simulation session to try it.

Contact: [Your name] · [email] · [phone] · github.com/Prithvi954/triage-evidence-worklist · demo video: [YouTube link]

---

## Email: nursing or medical school simulation center

> **Subject:** A free simulation tool for teaching triage and checking AI output
>
> Hi [Name],
>
> I'm [your name], a [year/major] at [school]. At EmoryHacks 2026 I built a training tool where nursing and medical students
> practice an urgent care shift with made-up patients. New notes arrive over time, an AI assistant points to what changed, and
> students have to check it against the real note, catch the AI's deliberate mistakes, decide whether flagged patients are real
> emergencies (with written reasons), and set priority. It scores them automatically with feedback, and students can build
> their own practice cases for the class.
>
> It's designed to be accessible (screen readers, keyboard, voice) and uses no real patient data.
>
> Here's a 2-minute demo: [link]. Would you have 30 minutes in the next few weeks to see whether it could fit a simulation
> session or course? I'd especially value your view on which scenarios would be most useful to students.
>
> Thank you,
> [Name]

## Email: residency program / clinical educator

> **Subject:** Teaching residents to verify AI in triage: short demo
>
> Dr. [Name],
>
> AI summaries are starting to appear in EHRs, and residents rarely get structured practice checking them. I built a simulation
> where learners review AI-highlighted evidence in clinical notes, catch planted AI errors, and make documented escalation and
> emergency decisions, with automatic scoring and instructor review.
>
> 2-minute demo: [link]. Could I show it to you or your chief residents for 20 minutes? I'm looking for feedback and a small
> pilot group.
>
> [Name]

## Email: hospital (nurse manager, ED director, innovation office)

> **Subject:** Accessible, AI-assisted note review: looking for clinical guidance
>
> Hi [Name],
>
> I'm developing an accessible triage worklist that helps nurses spot changes in new notes, with every decision staying with
> clinicians. It already includes encryption, access logging, break-the-glass for restricted records, and a read-only FHIR
> connector, and it refuses to send real notes to AI without a business associate agreement.
>
> I'm not asking to use patient data. I'm looking for 30 minutes of guidance from someone who runs a unit: what would make
> this useful, what would make it unsafe, and what a responsible pilot would need. Demo: [link].
>
> Thank you,
> [Name]

## Who to contact first

1. **Your own school's nursing or medical simulation center.** Ask a professor to introduce you.
2. **Emory School of Nursing / Emory School of Medicine simulation labs**, since you met them through EmoryHacks.
3. **Accessibility offices and disability student groups**, to test with screen-reader users (and credit their input).
4. **Hospital innovation offices** (for example Emory Healthcare's), only after an education pilot gives you feedback and users.

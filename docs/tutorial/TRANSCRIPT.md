# Video tour transcript

Narrated walkthrough of the live practice demo (made-up patients). Voice: Kokoro af_heart. Video: `tutorial.mp4` (subtitles built in, also `tutorial.vtt`). Regenerate with `.venv/bin/python -m scripts.make_tutorial`.


## Triage Evidence Worklist (00:00)
- **00:00** Welcome to the Triage Evidence Worklist. This is a practice demo with made-up patients. In about seven minutes, you'll see everything it does, step by step.

## 1 · Sign in (00:11)
- **00:11** First, choose who you are. Riley is a nurse. Sam is the charge nurse, who makes the final decisions.
- **00:19** We'll start as Riley.

## 2 · The screen (00:21)
- **00:21** This is the main screen. Everything fits on one page, so there's no long scrolling.
- **00:27** On the left are the pages: Patients, Emergencies, Notes to review, Overdue, Practice tools, and Help. The colored badges show how many things need attention.
- **00:38** Across the top is the system status, plus the Voice button and text-size buttons.
- **00:45** The blue box is your guide. It always tells you what to do next.
- **00:50** This is the care queue. The patient at the top is seen first. The colored edge and badge show how urgent each patient is. Level 1 is the most urgent, and purple means nobody has assessed them yet.

## 3 · A new note arrives (01:04)
- **01:04** Let's see what happens when a new note arrives. The guide says: send Patient B's new note.
- **01:11** A screen reader user hears a short message with no patient name, so nothing private is spoken out loud. And notice: the care queue did not change. The AI cannot move patients.

## 4 · Check the AI's work (01:23)
- **01:23** Let's open the new note. It opens on the right, and the list stays visible.
- **01:29** The yellow label says AI-assisted, unverified. A person always has to check it.
- **01:36** The first finding quotes the note exactly: Patient B now reports demo signal A. It also found that earlier, the patient denied it. So something changed.
- **01:47** The second finding is about the patient's mother, not the patient, so the AI kept it separate. A simple keyword search would get this wrong.

## 4 · Privacy (01:56)
- **01:56** Under What Claude received, you can see exactly what was sent to the AI: two short excerpts, with no name, no record number, and no dates.

## 4 · Your decision (02:05)
- **02:05** Now record your decision: Accept, Correct, or Reject. We'll accept. Your decision is saved with your name.

## 5 · Approval (02:12)
- **02:12** Only a charge nurse can change how urgent a patient is. Riley goes back to the list and keeps her place on Patient A.
- **02:20** Now Sam, in another window, approves Level 1 for Patient B. Watch the list.
- **02:27** Patient B moved to the top, but Riley's place stayed on Patient A. Pressing Enter opens Patient A, not someone else.

## 6 · Possible emergencies (02:35)
- **02:35** Next, emergencies. Clear, written rules can flag a possible emergency. Let's send a practice note where Patient D reports a warning sign.
- **02:45** A red banner appears, and the Emergencies badge lights up. A screen reader user hears an urgent alert, still without the patient's name.
- **02:55** A flag never moves a patient by itself. Any nurse can check it.
- **02:59** Open it to see why it was flagged: the rule, and the exact sentence from the note.
- **03:05** Riley checks the patient and answers yes or no. Writing a reason is required, so everyone can see why.
- **03:12** Save the check. It's added to the history, and the charge nurse can now see it.
- **03:17** Sam, the charge nurse, reads Riley's reason and makes the final decision: confirmed emergency. Sam can change it again later if things change.
- **03:28** Confirming the emergency moved Patient D to Level 1, and every step is saved with a name, a time, and a reason.

## 7 · Patient chart (03:36)
- **03:36** Now the patient chart. Like big hospital systems, it has tabs: Summary, Notes and documents, Write note, and Emergencies.
- **03:44** The Notes and documents tab shows every note, newest first.
- **03:48** Use the search bar to find words. Let's search for denies. Every match is highlighted in yellow.

## 7 · Write a note (03:55)
- **03:55** To write a note, open Write note. You can type, or press Start dictation and simply speak.
- **04:01** Notes you write or dictate stay on this server. They're never sent to the AI, and the emergency rules check them automatically.
- **04:09** Save it, and it appears in the chart with your name and the time.

## 8 · Voice (04:13)
- **04:13** The Voice button lets you control the app by speaking. Press Start listening and say something like: bring up patient C. You can also type the command.
- **04:23** And Patient C's chart opens. This helps nurses who can't easily use a mouse, or whose hands are busy.
- **04:30** Try other commands too, like take a note for patient B, search for denies, or open emergencies.

## 9 · Sticky notes (04:37)
- **04:37** Pin any note to keep it on screen as a sticky note while you look at other patients.

## 9 · Overdue tasks (04:42)
- **04:42** The Overdue page shows late tasks and who is responsible. If someone runs late, a backup is assigned automatically.

## 10 · If the AI stops working (04:51)
- **04:51** What if the AI stops working? Here we pretend it's down, and send another note.
- **04:57** The note still shows up, marked manual review required. Everything else keeps working without the AI.

## 11 · Help (05:03)
- **05:03** Need a reminder of what a word means? Open Help and words. And use the A buttons to make text bigger.

## You're ready (05:10)
- **05:10** That's the tour. Remember: the AI suggests, and people decide. To practice again, open Practice tools and press Reset all demo data.

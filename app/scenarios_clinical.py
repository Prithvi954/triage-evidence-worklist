"""Built-in practice scenarios with realistic (synthetic) clinical wording.

EDUCATIONAL CONTENT, PENDING CLINICAL REVIEW. Every patient and note here is made up. The answer
keys reflect common triage teaching (ESI 1-5 and widely taught red flags) and must be reviewed by a
licensed nurse or nurse educator before use in a course. They are not clinical guidance.

Answer key per patient: emergency (True/False/None = not scored), max_acuity (ESI level or more
urgent; scored for charge nurse / provider roles), escalate (True = should be escalated).
"""

REVIEW = "Pending review by a licensed nurse educator"


def P(key, age, sex, complaint, location, esi, emergency=None, max_acuity=None, escalate=None, restricted=False):
    return {"key": key, "label": f"Patient {key}", "age": age, "sex": sex, "complaint": complaint, "location": location,
            "priority": esi, "restricted": restricted,
            "answer": {"emergency": emergency, "max_acuity": max_acuity, "escalate": escalate}}


def N(key, patient, event, text, trap=None):
    return {"key": key, "patient": patient, "event": event, "text": text, "ai_trap": trap, "replay": None}


DENIED = {"field": "assertion", "correct": "denied"}
FAMILY = {"field": "subject", "correct": "family_member"}
PAST = {"field": "time_context", "correct": "past_resolved"}

CHEST = {
    "id": "sc_clin_chest", "title": "Chest pain in the waiting room", "role": "rn",
    "summary": "Three people in the lobby. One says it's just indigestion, until it isn't. One denies chest pain, "
               "and one only mentions a parent's heart attack.",
    "goals": "Recognize warning signs of a heart attack · Don't over-call denied symptoms or family history · Escalate early",
    "spec": {"clinical_review": REVIEW, "patients": [
        P("H", 58, "M", "Indigestion", "Lobby", 3, emergency=True, escalate=True),
        P("J", 34, "F", "Ankle injury", "Room 2", 4, emergency=False),
        P("K", 45, "M", "Blood pressure check", "Lobby", 5, emergency=False)],
     "notes": [
        N("H0", "H", 0, "Triage note. Patient reports indigestion since lunch, discomfort 3 out of 10. Vital signs within normal limits."),
        N("J0", "J", 0, "Triage note. Twisted right ankle playing soccer. Able to bear weight with a limp. Pain 5 out of 10."),
        N("H1", "H", 1, "Reassessment. Patient now reports chest pain spreading to the left arm. Skin is pale and he is sweating. "
                        "Patient also reports nausea."),
        N("K1", "K", 1, "Triage note. Here for a blood pressure check. Patient's father had chest pain and a heart attack at age 60. "
                        "Patient reports no symptoms today.", FAMILY),
        N("J2", "J", 2, "Reassessment. Patient denies chest pain and shortness of breath. Ice applied, pain now 3 out of 10.", DENIED)]},
}

STROKE = {
    "id": "sc_clin_stroke", "title": "Stroke signs: every minute counts", "role": "charge_rn",
    "summary": "A dizzy older patient develops new face droop and slurred speech. Another had similar signs last year that "
               "fully went away. A third has a sore throat.",
    "goals": "Spot stroke signs (face, arm, speech, time) · Tell new signs from old resolved ones · Set ESI correctly",
    "spec": {"clinical_review": REVIEW, "patients": [
        P("L", 72, "F", "Dizziness", "Lobby", 3, emergency=True, max_acuity=2, escalate=True),
        P("M", 66, "M", "Headache", "Room 3", 3, emergency=False),
        P("N", 29, "F", "Sore throat", "Room 5", 5, emergency=False)],
     "notes": [
        N("L0", "L", 0, "Triage note. Patient reports feeling dizzy since this morning. Speech clear. Moves all four limbs."),
        N("N0", "N", 0, "Triage note. Sore throat for two days. Drinking fluids. Breathing comfortably."),
        N("L1", "L", 1, "Reassessment. Family says her face looks uneven. New facial droop on the left side and slurred speech "
                        "first noticed ten minutes ago. Left arm weakness present."),
        N("M1", "M", 1, "Triage note. Mild headache for two days. Slurred speech and arm weakness last year, fully resolved. "
                        "Today speech is clear and strength is equal on both sides.", PAST),
        N("N2", "N", 2, "Reassessment. Sore throat unchanged. Patient denies trouble breathing.")]},
}

SEPSIS = {
    "id": "sc_clin_sepsis", "title": "Is it sepsis?", "role": "charge_rn",
    "summary": "An older patient with a urinary infection becomes confused with low blood pressure. A younger patient had a "
               "fever last week that went away.",
    "goals": "Recognize possible sepsis (infection + confusion or low blood pressure) · Read the timing · Move the right patient up",
    "spec": {"clinical_review": REVIEW, "patients": [
        P("P", 81, "F", "Urinary symptoms", "Room 4", 3, emergency=True, max_acuity=2, escalate=True),
        P("Q", 50, "M", "Cough", "Lobby", 4, emergency=False)],
     "notes": [
        N("P0", "P", 0, "Triage note. Burning with urination for three days. Alert and talking normally."),
        N("Q1", "Q", 1, "Triage note. Cough for one week. Reports fever last week that resolved without medicine. "
                        "No confusion. Breathing comfortably.", PAST),
        N("P2", "P", 2, "Reassessment. Patient has a fever and is now confused, not sure where she is. Blood pressure low "
                        "at 86 over 50. Heart rate 118.")]},
}

BREATH = {
    "id": "sc_clin_breathing", "title": "Can't catch a breath", "role": "rn",
    "summary": "A young adult with asthma gets worse in the lobby. Another patient has an allergic rash but no airway signs.",
    "goals": "Recognize severe breathing trouble · Check airway signs in allergic reactions · Read 'no' and 'denies' carefully",
    "spec": {"clinical_review": REVIEW, "patients": [
        P("R", 19, "M", "Asthma", "Lobby", 3, emergency=True, escalate=True),
        P("S", 40, "F", "Allergic reaction", "Room 6", 3, emergency=False)],
     "notes": [
        N("R0", "R", 0, "Triage note. History of asthma. Used inhaler twice this morning with some relief. Speaking in full sentences."),
        N("S0", "S", 0, "Triage note. Itchy rash on both arms after eating shrimp. No throat tightness. No trouble breathing."),
        N("R1", "R", 1, "Reassessment. Patient now has trouble breathing and is unable to speak in full sentences. "
                        "Loud wheezing heard. Inhaler not helping."),
        N("S2", "S", 2, "Reassessment. Rash fading after medication. Patient denies throat tightness and trouble breathing.", DENIED)]},
}

SHIFT = {
    "id": "sc_clin_shift", "title": "Saturday evening shift", "role": "charge_rn",
    "summary": "Five patients, four updates. Chest pain, stroke signs, a resolved fever, an allergy without airway signs, "
               "and a sore throat. Used for the training users (practice as Riley, Sam or Dr. Lee).",
    "goals": "Work a full board · Review every note · Catch the real emergencies and leave the rest",
    "spec": {"clinical_review": REVIEW, "patients": [
        P("H", 58, "M", "Indigestion", "Lobby", 3, emergency=True, max_acuity=2, escalate=True),
        P("L", 72, "F", "Dizziness", "Room 1", 3, emergency=True, max_acuity=2, escalate=True),
        P("Q", 50, "M", "Cough", "Lobby", 4, emergency=False),
        P("S", 40, "F", "Allergic reaction", "Room 6", 3, emergency=False),
        P("N", 29, "F", "Sore throat", "Room 5", 5, emergency=False)],
     "notes": [
        N("H0", "H", 0, CHEST["spec"]["notes"][0]["text"]),
        N("L0", "L", 0, STROKE["spec"]["notes"][0]["text"]),
        N("S0", "S", 0, BREATH["spec"]["notes"][1]["text"]),
        N("N0", "N", 0, STROKE["spec"]["notes"][1]["text"]),
        N("Q1", "Q", 1, SEPSIS["spec"]["notes"][1]["text"], PAST),
        N("H2", "H", 2, CHEST["spec"]["notes"][2]["text"]),
        N("S3", "S", 3, BREATH["spec"]["notes"][3]["text"], DENIED),
        N("L4", "L", 4, STROKE["spec"]["notes"][2]["text"])]},
}

CLINICAL_SCENARIOS = [CHEST, STROKE, SEPSIS, BREATH, SHIFT]

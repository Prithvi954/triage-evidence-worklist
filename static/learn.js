"use strict";

let ME = null;
let editingId = null;
const STATUS_TEXT = { builtin: "Built-in", approved: "In class library", draft: "Draft (private)", submitted: "Waiting for review" };

function showTab(name, focus = true) {
  document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  $("editor").hidden = true;
  document.querySelectorAll(".subnav button").forEach((b) => {
    if (b.dataset.tab === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  const loaders = { library: loadLibrary, mine: loadLibrary, review: loadLibrary, results: loadResults, students: loadStudents };
  if (loaders[name]) loaders[name]();
  if (focus) { const h = document.querySelector(`[data-panel="${name}"] h1`); if (h) h.focus(); }
}

function scenarioCard(s) {
  const btns = [el("button", { type: "button", class: "primary", onclick: () => startPractice(s) }, `Start practice: ${s.title}`)];
  if (s.mine && s.status === "draft") btns.push(el("button", { type: "button", onclick: () => submitCase(s) }, "Submit to class"));
  if (s.mine || (ME.role === "instructor" && s.status !== "builtin")) {
    btns.push(el("button", { type: "button", onclick: () => openEditor(s.id) }, "Edit"));
  }
  if (ME.role === "instructor" && s.status === "submitted") {
    btns.push(el("button", { type: "button", onclick: () => reviewCase(s, "approve") }, "Approve"),
      el("button", { type: "button", onclick: () => reviewCase(s, "return") }, "Return with comment"));
  }
  if ((s.mine && s.status !== "approved") || (ME.role === "instructor" && s.status !== "builtin")) {
    btns.push(el("button", { type: "button", class: "danger", onclick: () => deleteCase(s) }, "Delete"));
  }
  return el("article", { class: "sc-card", "aria-labelledby": `sc-${s.id}` },
    el("h3", { id: `sc-${s.id}` }, s.title),
    el("p", { class: "sc-meta" }, `${STATUS_TEXT[s.status] || s.status} · by ${s.author} · you play: ${s.role_text} · ${s.patients} patients · ${s.events} events`),
    el("p", {}, s.summary),
    el("p", { class: "sc-meta" }, `Goals: ${s.goals}`),
    s.ai_enabled ? null : el("p", { class: "sc-meta" }, "AI evidence is off until an instructor approves this case."),
    s.review_comment ? el("p", { class: "warn" }, `Instructor note: ${s.review_comment}`) : null,
    el("div", { class: "btns" }, btns));
}

async function loadLibrary() {
  const list = await api("GET", "/api/learn/scenarios");
  $("lib-cards").replaceChildren(...list.filter((s) => s.status === "builtin" || s.status === "approved").map(scenarioCard));
  const mine = list.filter((s) => s.mine);
  $("mine-cards").replaceChildren(...(mine.length ? mine.map(scenarioCard) : [el("p", { class: "empty" }, "No cases yet. Build your first one.")]));
  const rev = list.filter((s) => s.status === "submitted");
  $("rev-cards").replaceChildren(...(rev.length ? rev.map(scenarioCard) : [el("p", { class: "empty" }, "Nothing waiting for review.")]));
}

async function startPractice(s) {
  say(`Starting ${s.title}…`);
  const r = await api("POST", `/api/learn/scenarios/${s.id}/start`, {});
  location.href = r.home || "/";
}

async function submitCase(s) {
  try { await api("POST", `/api/learn/scenarios/${s.id}/submit`, {}); say("Submitted to your instructor."); loadLibrary(); }
  catch (e) { say(e.message); }
}

async function reviewCase(s, action) {
  let comment = "";
  if (action === "return") {
    comment = await promptText(`Return “${s.title}”`, "What should the student change?");
    if (comment === null) return;
  }
  try { await api("POST", `/api/learn/scenarios/${s.id}/review`, { action, comment }); say(action === "approve" ? "Approved and added to the class library." : "Returned to the student."); loadLibrary(); }
  catch (e) { say(e.message); }
}

async function deleteCase(s) {
  const ok = await promptText(`Delete “${s.title}”?`, "Type DELETE to confirm.");
  if (ok === null || ok.trim().toUpperCase() !== "DELETE") return;
  try { await api("POST", `/api/learn/scenarios/${s.id}/delete`, {}); say("Deleted."); loadLibrary(); }
  catch (e) { say(e.message); }
}

function promptText(title, label) {
  return new Promise((resolve) => {
    const prev = document.activeElement;
    const ta = el("textarea", { id: "prompt-text", rows: 3 });
    const close = (v) => { back.remove(); if (prev && prev.focus) prev.focus(); resolve(v); };
    const form = el("form", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "prompt-h",
      onsubmit: (e) => { e.preventDefault(); close(ta.value); } },
      el("h2", { id: "prompt-h" }, title), el("label", { for: "prompt-text" }, label), ta,
      el("div", { class: "btns" }, el("button", { type: "submit", class: "primary" }, "OK"), el("button", { type: "button", onclick: () => close(null) }, "Cancel")));
    form.addEventListener("keydown", (e) => { if (e.key === "Escape") close(null); });
    const back = el("div", { class: "modal-back" }, form);
    document.body.append(back);
    ta.focus();
  });
}

// ---------------------------------------------------------------- results
async function loadResults() {
  const rows = await api("GET", "/api/learn/sessions");
  $("res-cap").textContent = ME.role === "instructor" ? `${rows.length} practice sessions in your class` : `${rows.length} of your practice sessions`;
  $("res-body").replaceChildren(...(rows.length ? rows.map((r) => el("tr", {},
    el("td", {}, r.student), el("td", {}, r.scenario), el("td", {}, ymdhm(r.started_at)),
    el("td", {}, r.percent === null ? "In progress" : `${r.percent}% (${r.points}/${r.possible})`),
    el("td", {}, r.finished_at ? el("button", { type: "button", onclick: () => showResult(r.id) }, `View ${r.student}'s result`) : "")))
    : [el("tr", {}, el("td", { colspan: "5" }, "No practice sessions yet."))]));
}

async function showResult(id) {
  const r = await api("GET", `/api/learn/sessions/${id}`);
  const sc = r.score;
  const outcome = { correct: ["result-ok", "Correct"], missed: ["result-miss", "Missed"], partial: ["result-part", "Partly"], not_scored: ["sc-meta", "Not scored"] };
  const box = $("result-detail");
  box.replaceChildren(el("section", { class: "panel", "aria-labelledby": "rd-h" },
    el("h2", { id: "rd-h", tabindex: "-1" }, `${r.scenario}: ${r.student}`),
    sc ? el("p", {}, el("span", { class: "score-big" }, `${sc.percent}%`), `  ${sc.points} of ${sc.possible} points · events completed ${sc.events_completed}/${sc.events_total}`
      + (sc.avg_review_minutes !== null ? ` · average time to review a note: ${sc.avg_review_minutes} min` : "")) : el("p", {}, "Not finished."),
    sc ? el("div", { class: "table-wrap" }, el("table", {}, el("caption", {}, "Feedback"),
      el("thead", {}, el("tr", {}, ...["Patient", "Skill", "Result", "Points", "Feedback"].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, ...sc.items.map((i) => el("tr", {}, el("td", {}, i.patient), el("td", {}, i.area),
        el("td", { class: outcome[i.outcome][0] }, outcome[i.outcome][1]), el("td", {}, `${i.points}/${i.max}`), el("td", {}, i.feedback)))))) : null,
    r.emergency_reasons.length ? [el("h3", {}, "Emergency reasons written"), el("ul", {}, r.emergency_reasons.map((e) =>
      el("li", {}, `${e.label}: ${e.decision ? e.decision.replace(/_/g, " ") : e.action.replace(/_/g, " ")}${e.reason ? ` — “${e.reason}”` : ""}`)))] : null,
    r.decisions.length ? [el("h3", {}, "Note review decisions"), el("ul", {}, r.decisions.map((d) =>
      el("li", {}, `${d.patient}: ${d.decision}${d.corrected.length ? ` (corrected ${d.corrected.join(", ")})` : ""}${d.comment ? ` — “${d.comment}”` : ""}`)))] : null));
  $("rd-h").focus();
}

// ---------------------------------------------------------------- students (instructor)
async function loadStudents() {
  const users = await api("GET", "/api/admin/users");
  $("stu-body").replaceChildren(...users.map((u) => el("tr", {}, el("td", {}, u.name), el("td", {}, u.username || "—"),
    el("td", {}, u.role_text), el("td", {}, u.title))));
}

// ---------------------------------------------------------------- case builder
function field(label, input) { return el("label", {}, label, input); }
function triSelect(value) {
  const s = el("select", {}, el("option", { value: "" }, "Not scored"), el("option", { value: "yes" }, "Yes"), el("option", { value: "no" }, "No"));
  s.value = value === true ? "yes" : value === false ? "no" : "";
  return s;
}

function patientRow(p, idx) {
  p = p || {};
  const row = el("fieldset", { class: "editor-row ed-patient" }, el("legend", {}, `Patient ${idx + 1}`));
  const inputs = {
    key: el("input", { type: "text", value: p.key || String.fromCharCode(72 + idx), maxlength: 8, size: 4 }),
    label: el("input", { type: "text", value: p.label || `Patient ${String.fromCharCode(72 + idx)}`, maxlength: 20 }),
    age: el("input", { type: "number", min: 0, max: 120, value: p.age ?? "" }),
    sex: el("select", {}, el("option", { value: "F" }, "F"), el("option", { value: "M" }, "M"), el("option", { value: "X" }, "X")),
    complaint: el("input", { type: "text", value: p.complaint || "Demo concern X", maxlength: 60 }),
    location: el("input", { type: "text", value: p.location || "Lobby", maxlength: 30 }),
    priority: el("select", {}, el("option", { value: "" }, "Unassessed"), ...[1, 2, 3, 4].map((n) => el("option", { value: n }, `Level ${n}`))),
    restricted: el("input", { type: "checkbox" }),
    emergency: triSelect(p.answer && p.answer.emergency),
    max_acuity: el("select", {}, el("option", { value: "" }, "Not scored"), ...[1, 2, 3, 4].map((n) => el("option", { value: n }, `Level ${n} or more urgent`))),
    escalate: triSelect(p.answer && p.answer.escalate),
  };
  inputs.sex.value = p.sex || "F";
  inputs.priority.value = p.priority ?? "";
  inputs.restricted.checked = !!p.restricted;
  inputs.max_acuity.value = (p.answer && p.answer.max_acuity) ?? "";
  row._inputs = inputs;
  row.append(field("Key", inputs.key), field("Label", inputs.label), field("Age", inputs.age), field("Sex", inputs.sex),
    field("Chief complaint", inputs.complaint), field("Location", inputs.location), field("Starting acuity", inputs.priority),
    field("Restricted record", inputs.restricted),
    field("Answer: is this an emergency?", inputs.emergency), field("Answer: acuity should be", inputs.max_acuity),
    field("Answer: should escalate?", inputs.escalate),
    el("button", { type: "button", class: "danger", onclick: () => { row.remove(); renumber(); } }, `Remove patient ${idx + 1}`));
  return row;
}

function noteRow(n, idx) {
  n = n || {};
  const row = el("fieldset", { class: "editor-row ed-note" }, el("legend", {}, `Note ${idx + 1}`));
  const inputs = {
    key: el("input", { type: "text", value: n.key || `N${idx + 1}`, maxlength: 8, size: 4 }),
    patient: el("input", { type: "text", value: n.patient || "H", maxlength: 8, size: 4 }),
    event: el("input", { type: "number", min: 0, max: 10, value: n.event ?? 1 }),
    text: el("textarea", { rows: 2, maxlength: 1500 }),
    trap: el("select", {}, el("option", { value: "" }, "None"),
      el("option", { value: "assertion:denied" }, "Correct reading: denied"),
      el("option", { value: "assertion:reported" }, "Correct reading: reported"),
      el("option", { value: "subject:family_member" }, "About a family member"),
      el("option", { value: "time_context:past_resolved" }, "In the past, resolved")),
  };
  inputs.text.value = n.text || "";
  inputs.trap.value = n.ai_trap ? `${n.ai_trap.field}:${n.ai_trap.correct}` : "";
  row._inputs = inputs;
  row.append(field("Key", inputs.key), field("Patient key", inputs.patient), field("Event", inputs.event),
    field("Check that learners catch an AI misreading", inputs.trap),
    el("label", { class: "wide" }, "Note text", inputs.text),
    el("button", { type: "button", class: "danger", onclick: () => { row.remove(); renumber(); } }, `Remove note ${idx + 1}`));
  return row;
}

function renumber() {
  [...$("ed-patients").children].forEach((r, i) => { r.querySelector("legend").textContent = `Patient ${i + 1}`; });
  [...$("ed-notes").children].forEach((r, i) => { r.querySelector("legend").textContent = `Note ${i + 1}`; });
}

async function openEditor(id) {
  editingId = id || null;
  document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = true; });
  $("editor").hidden = false;
  $("ed-result").textContent = "";
  let s = { title: "", summary: "", goals: "", role: "rn", spec: { patients: [{}], notes: [
    { key: "N1", patient: "H", event: 0, text: "Triage note. Patient reports demo signal B, mild." },
    { key: "N2", patient: "H", event: 1, text: "Nursing note. Patient now reports demo red flag R." }] } };
  if (id) s = await api("GET", `/api/learn/scenarios/${id}`);
  $("ed-title").value = s.title; $("ed-summary").value = s.summary || ""; $("ed-goals").value = s.goals || ""; $("ed-role").value = s.role;
  $("ed-attest").checked = false;
  $("ed-patients").replaceChildren(...s.spec.patients.map((p, i) => patientRow(p, i)));
  $("ed-notes").replaceChildren(...s.spec.notes.map((n, i) => noteRow(n, i)));
  $("ed-h").textContent = id ? `Edit case: ${s.title}` : "Build a new case";
  $("ed-h").focus();
}

function collect() {
  const patients = [...$("ed-patients").children].map((r) => {
    const x = r._inputs;
    const tri = (v) => (v === "yes" ? true : v === "no" ? false : null);
    return { key: x.key.value.trim(), label: x.label.value.trim(), age: x.age.value, sex: x.sex.value, complaint: x.complaint.value,
      location: x.location.value, priority: x.priority.value, restricted: x.restricted.checked,
      answer: { emergency: tri(x.emergency.value), max_acuity: x.max_acuity.value, escalate: tri(x.escalate.value) } };
  });
  const notes = [...$("ed-notes").children].map((r) => {
    const x = r._inputs;
    const [f, c] = x.trap.value ? x.trap.value.split(":") : [null, null];
    return { key: x.key.value.trim(), patient: x.patient.value.trim(), event: x.event.value, text: x.text.value,
      ai_trap: f ? { field: f, correct: c } : null };
  });
  return { title: $("ed-title").value, summary: $("ed-summary").value, goals: $("ed-goals").value, role: $("ed-role").value,
    synthetic_attested: $("ed-attest").checked, spec: { patients, notes } };
}

document.addEventListener("DOMContentLoaded", async () => {
  ME = await pageInit(["student", "instructor"]);
  if (ME.role === "instructor") {
    document.querySelector('[data-tab="review"]').hidden = false;
    document.querySelector('[data-tab="students"]').hidden = false;
    document.querySelector('[data-tab="mine"]').textContent = "My scenarios";
  }
  document.querySelectorAll(".subnav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("new-case").addEventListener("click", () => openEditor(null));
  $("add-patient").addEventListener("click", () => { $("ed-patients").append(patientRow(null, $("ed-patients").children.length)); });
  $("add-note").addEventListener("click", () => { $("ed-notes").append(noteRow(null, $("ed-notes").children.length)); });
  $("ed-cancel").addEventListener("click", () => showTab("mine"));
  $("ed-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("ed-result");
    try {
      const r = await api("POST", editingId ? `/api/learn/scenarios/${editingId}` : "/api/learn/scenarios", collect());
      editingId = r.id;
      out.textContent = r.status === "approved" ? "Saved and added to the class library." : "Saved as a private draft. Start practice from My cases, or submit it to your class.";
      say(out.textContent);
    } catch (err) { out.textContent = err.message; out.focus(); }
  });
  $("stu-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("stu-result");
    try {
      await api("POST", "/api/admin/users", { name: $("stu-name").value, username: $("stu-user").value, role: $("stu-role").value,
        title: $("stu-title").value, password: $("stu-pass").value });
      out.textContent = `Added ${$("stu-name").value}. Share the temporary password privately.`; e.target.reset(); loadStudents();
    } catch (err) { out.textContent = err.message; }
    out.focus();
  });
  $("welcome-name").textContent = ME.name.split(" ")[0];
  loadPersonas();
  const m = location.hash.match(/result=([A-Za-z0-9_-]+)/);
  if (m) { showTab("results", false); showResult(m[1]); } else showTab("library");
});

async function loadPersonas() {
  const list = await api("GET", "/api/learn/personas");
  $("personas").replaceChildren(...list.map((p) => el("article", { class: "persona", "aria-labelledby": `pz-${p.id}` },
    el("span", { class: "p-av", "aria-hidden": "true" }, p.name.replace("Dr. ", "")[0]),
    el("h3", { id: `pz-${p.id}` }, `${p.name}`),
    el("p", { class: "sc-meta" }, p.who),
    el("p", {}, p.does),
    el("div", { class: "btns" }, el("button", { type: "button", class: "primary", onclick: async () => {
      say(`Opening the practice unit as ${p.name}…`);
      try { const r = await api("POST", `/api/learn/personas/${p.id}/start`, {}); location.href = r.home || "/"; }
      catch (e) { say(e.message); }
    } }, `Practice as ${p.name}`)))));
}

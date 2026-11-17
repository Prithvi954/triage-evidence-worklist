// Triage Evidence Worklist client. Synthetic demo. All content is set with textContent (no HTML injection).
"use strict";

const $ = (id) => document.getElementById(id);
let S = null;                 // last /api/state
let es = null;                // EventSource
let connState = "connecting";
let staleSince = null;
const seenSeq = new Set();
const announcedItems = new Set();
let pending = [];             // announcements waiting to be grouped
let pendingTimer = null;
let openDetail = null;        // {kind: "encounter"|"item"|"escalate", id, encounterId, label}
let lastReviewed = null;      // {encounterId, label}
let fromRowKey = null;        // queue row that opened the detail panel
const drafts = {};            // item_id -> {decision, comment, corrections:{findingId:{field:value}}}
let refreshTimer = null;

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

function hhmm(iso) { return iso ? iso.slice(11, 16) : ""; }
function mins(n) { return n < 0 ? `overdue by ${-n} min` : `in ${n} min`; }

async function api(method, path, body) {
  const opt = { method, headers: { "X-Requested-With": "triage-ui" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { data = null; }
  if (r.status === 401) { showLogin(); throw new Error("unauthenticated"); }
  if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return data;
}

// ---------------------------------------------------------------- announcements
function say(msg, itemId) {
  if (!msg) return;
  // One entry per note: a later "evidence ready" replaces the earlier "note received" for the same item.
  const i = itemId ? pending.findIndex((p) => p.item === itemId) : -1;
  if (i >= 0) pending[i] = { item: itemId, msg }; else pending.push({ item: itemId || null, msg });
  if (!pendingTimer) pendingTimer = setTimeout(flushAnnouncements, 1200);   // group bursts (surge)
}
function sayUrgent(msg) {   // assertive: reserved for possible emergencies (important and time-sensitive)
  const live = $("announcer-urgent");
  live.textContent = "";
  setTimeout(() => { live.textContent = msg; }, 50);
  $("latest-text").textContent = `${msg} (${hhmm(S && S.now)})`;
}
function flushAnnouncements() {
  pendingTimer = null;
  const msgs = pending.map((p) => p.msg); pending = [];
  if (!msgs.length) return;
  const noteMsgs = msgs.filter((m) => /awaiting clinical review/.test(m));
  const other = [...new Set(msgs.filter((m) => !/awaiting clinical review/.test(m)))];
  const parts = [];
  if (noteMsgs.length === 1) parts.push(noteMsgs[0]);
  else if (noteMsgs.length > 1 && noteMsgs.some((m) => /^New evidence/.test(m))) {
    const last = noteMsgs[noteMsgs.length - 1].match(/(\d+) unreviewed/);
    parts.push(`${noteMsgs.length} new notes awaiting clinical review, including new evidence.` + (last ? ` ${last[1]} unreviewed in evidence inbox.` : ""));
  }
  else if (noteMsgs.length > 1) {
    const last = noteMsgs[noteMsgs.length - 1].match(/(\d+) unreviewed/);
    parts.push(`${noteMsgs.length} new notes awaiting clinical review.` + (last ? ` ${last[1]} unreviewed in evidence inbox.` : ""));
  }
  parts.push(...other);
  const text = parts.join(" ");
  const live = $("announcer");
  live.textContent = "";
  setTimeout(() => { live.textContent = text; }, 50);   // re-set so identical text is still spoken
  $("latest-text").textContent = `${text} (${hhmm(S && S.now)})`;
}

// ---------------------------------------------------------------- login
async function showLogin() {
  $("app").hidden = true; $("login").hidden = false;
  if (es) { es.close(); es = null; }
  let cfg = { demo_login: true };
  try { cfg = await (await fetch("/api/config")).json(); } catch (_) { /* keep defaults */ }
  const ul = $("login-list"); ul.replaceChildren();
  $("demo-users").hidden = !cfg.demo_login;
  if (cfg.demo_login) {
    const ORDER = ["u_riley", "u_sam", "u_drlee", "u_jordan", "u_morgan", "u_taylor", "u_avery", "u_alex", "u_casey"];
    const users = (await (await fetch("/api/demo-users")).json())
      .sort((a, b) => ((ORDER.indexOf(a.id) + 99) % 99) - ((ORDER.indexOf(b.id) + 99) % 99));
    for (const u of users) {
      const descId = `ud-${u.id}`;
      ul.append(el("li", { class: "login-card" },
        el("button", { type: "button", class: u.id === "u_riley" ? "primary big" : "big", "aria-describedby": descId,
          onclick: () => login({ user_id: u.id }) }, `Sign in as ${u.label}`),
        el("p", { id: descId, class: "hint" }, USER_HELP[u.id] || "")));
    }
  }
  $("login-h").focus();
}

async function login(creds) {
  let r;
  try { r = await api("POST", "/api/login", creds); }
  catch (e) { $("login-error").textContent = e.message === "forbidden" ? "Sign-in failed." : e.message; $("login-pass").focus(); return; }
  if (r.home && r.home !== "/") { location.href = r.home; return; }
  $("login").hidden = true; $("app").hidden = false;
  await refresh();
  connect();
  loadFixtures();
  $("queue-h").focus();
}

// ---------------------------------------------------------------- live updates
function connect() {
  if (es) es.close();
  es = new EventSource("/api/events");          // browser resends Last-Event-ID on reconnect
  es.onopen = () => {
    const wasDown = connState === "down";
    connState = "up"; staleSince = null; renderStatus();
    if (wasDown) { refresh().then(() => say("Live updates reconnected. Data refreshed.")); }
  };
  es.onerror = () => {
    if (connState !== "down") { connState = "down"; staleSince = S ? S.now : null; renderStatus();
      say("Live updates disconnected. Data on screen may be out of date."); }
  };
  es.addEventListener("update", (ev) => {
    const d = JSON.parse(ev.data);
    if (seenSeq.has(d.seq)) return;              // duplicate suppression
    seenSeq.add(d.seq);
    const itemId = d.ids && d.ids.item_id;
    if (d.announce) {
      const key = `${d.type}:${itemId || d.seq}`;
      if (!announcedItems.has(key)) { announcedItems.add(key); if (d.urgent) sayUrgent(d.announce); else say(d.announce, itemId); }
    }
    scheduleRefresh();
  });
  es.addEventListener("access_revoked", () => accessEnded());
}
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 250); }

function accessEnded() {
  if (es) { es.close(); es = null; }
  $("detail-body").replaceChildren(); $("queue").replaceChildren(); $("inbox").replaceChildren(); $("ob-body").replaceChildren();
  pins.length = 0; renderPins(); $("app").classList.remove("has-detail"); $("detail").hidden = true;
  $("em-list").replaceChildren(); $("emerg-banner").hidden = true; stopDictation();
  $("app").hidden = true; $("login").hidden = false;
  $("login-h").textContent = "Your access has ended. Patient information has been cleared from this screen.";
  $("login-list").replaceChildren();
  $("login-h").focus();
  say("Your access has ended. Patient information was cleared from this screen.");
}

// ---------------------------------------------------------------- state and rendering
async function refresh() {
  let st;
  try { st = await api("GET", "/api/state"); }
  catch (e) { if (e.status === 403) accessEnded(); return; }
  S = st;
  renderStatus(); renderQueue(); renderInbox(); renderOverdue(); checkOpenDetail(); renderGuide();
  loadEmergencies();
}

function renderStatus() {
  if (!S) return;
  const m = S.mode;
  $("s-mode").textContent = (m.setting === "live" ? `Live Claude calls (${m.model})` : "Replay, not a live Claude call")
    + (m.fault !== "none" ? `, simulating: ${m.fault.replace("_", " ")}` : "");
  const lc = m.last_call;
  $("s-call").textContent = !lc ? "None yet" : `${shortProvenance(lc.provenance)}: ${lc.outcome.replace(/_/g, " ")}, ${hhmm(lc.finished_at)}`;
  $("s-call").title = lc ? describeProvenance(lc.provenance, lc.outcome) : "";
  $("s-feed").textContent = S.feed.stale ? `May be stalled (last ${hhmm(S.feed.last_message_at)})`
    : `OK (last ${hhmm(S.feed.last_message_at)})`;
  $("s-conn").textContent = connState === "up" ? "Connected" : connState === "down"
    ? `Disconnected${staleSince ? " since " + hhmm(staleSince) : ""}. Data may be out of date` : "Connecting";
  $("s-clock").textContent = `${hhmm(S.now)} (practice)`;
  $("s-user").textContent = `${S.me.name}, ${S.me.title}`;
  $("avatar").textContent = S.me.name[0];
  $("s-feed").closest(".st").classList.toggle("is-bad", !!S.feed.stale);
  $("s-conn").closest(".st").classList.toggle("is-bad", connState === "down");
  $("s-mode").closest(".st").classList.toggle("is-live", m.setting === "live");
  $("nav-patients").textContent = S.queue.length || "";
  $("nav-inbox").textContent = S.unreviewed || "";
  $("nav-overdue").textContent = S.overdue.length || "";
  $("queue-rule").textContent = S.queue_rule;
  $("inbox-rule").textContent = S.inbox_rule;
}

function shortProvenance(p) {
  if (!p) return "Unknown";
  if (p.startsWith("replay")) return "Replayed, not live";
  if (p.startsWith("live_call")) return "Live call";
  return p;
}
function describeProvenance(p, outcome) {
  if (!p) return outcome;
  if (p.startsWith("replay:authored")) return `Replayed authored fixture (not a live call), outcome ${outcome}`;
  if (p.startsWith("replay:captured")) return `Replayed captured response (not a live call), outcome ${outcome}`;
  if (p.startsWith("replay")) return `Replay mode, outcome ${outcome}`;
  if (p.startsWith("live_call")) return outcome === "findings" || outcome === "no_qualifying_finding" ||
    outcome === "insufficient_context" || outcome === "conflicting_evidence"
    ? `Live Claude call succeeded, outcome ${outcome}` : `Live Claude call, outcome ${outcome}`;
  return outcome;
}

// Keyed reconciliation that never detaches the focused row, so focus stays on the same patient.
function reconcile(container, items, key, build, update) {
  const active = document.activeElement;
  const byKey = new Map([...container.children].map((c) => [c.dataset.key, c]));
  const desired = items.map((it) => { const k = key(it); const e = byKey.get(k) || build(it); e.dataset.key = k; update(e, it); return e; });
  const keep = new Set(desired);
  let removedFocused = null;
  for (const c of [...container.children]) if (!keep.has(c)) { if (c.contains(active)) removedFocused = c.dataset.key; c.remove(); }
  const anchor = desired.find((e) => e.contains(active));
  if (!anchor) desired.forEach((e) => container.appendChild(e));
  else {
    const i = desired.indexOf(anchor);
    for (let j = 0; j < i; j++) container.insertBefore(desired[j], anchor);
    for (let j = i + 1; j < desired.length; j++) container.appendChild(desired[j]);
  }
  return removedFocused;
}

function renderQueue() {
  const n = S.queue.length;
  $("queue-h").textContent = `Care queue, official order (${n} waiting)`;
  const removed = reconcile($("queue"), S.queue, (q) => q.encounter_id,
    (q) => {
      const li = el("li");
      const desc = el("span", { class: "desc visually-hidden", id: `qd-${q.encounter_id}` });
      const cell = (cls) => el("span", { class: `cell ${cls}`, "aria-hidden": "true" });
      const open = el("button", { type: "button", class: "open", "aria-describedby": desc.id, onclick: () => openEncounter(q.encounter_id) },
        el("span", { class: "visually-hidden" }, "Review "), el("span", { class: "pt-name" }));
      const esc = el("button", { type: "button", class: "esc", onclick: () => openEscalate(q.encounter_id, q.label) },
        "Escalate", el("span", { class: "visually-hidden" }, ` concern for ${q.label}`));
      li.append(cell("c-acu"), open, cell("c-demo"), cell("c-comp"), cell("c-loc"), cell("c-arr"), cell("c-due"), cell("c-rn"),
        cell("c-flags"), desc, esc);
      return li;
    },
    (li, q) => {
      const disp = q.display || {};
      li.querySelector(".pt-name").textContent = shownName(q.label);
      const parts = [`Position ${q.position} of ${n}.`, `${q.priority_text}` +
        (q.priority_confirmed_by ? `, confirmed by ${q.priority_confirmed_by.split(",")[0]} at ${hhmm(q.priority_confirmed_at)}.` : ", no confirmed priority yet."),
        disp.age ? `${disp.age} ${disp.sex === "F" ? "female" : "male"}, ${disp.complaint}, ${disp.location}.` : "",
        `Primary: ${q.primary}.`];
      for (const o of q.obligations) parts.push(`${cap(o.kind_text)} due ${hhmm(o.due_at)}, ${mins(o.minutes)}` +
        (o.overdue ? ", OVERDUE." : ".") + (o.kind === "bedside_review" && !o.accepted_by ? " Not yet accepted." : ""));
      parts.push(`Unreviewed notes: ${q.unreviewed_notes}.`);
      if (q.emergency) parts.unshift(`${q.emergency}.`);
      li.querySelector(".desc").textContent = parts.filter(Boolean).join(" ");
      const overdue = q.obligations.some((o) => o.overdue);
      li.classList.toggle("has-overdue", overdue);
      li.classList.toggle("is-open", !!(openDetail && openDetail.encounterId === q.encounter_id));
      const lv = q.priority ? String(q.priority) : "u";
      li.querySelector(".c-acu").replaceChildren(el("span", { class: `acu acu-${lv}` }, q.priority ? String(q.priority) : "U"));
      li.querySelector(".c-demo").textContent = disp.age ? `${disp.age} ${disp.sex}` : "";
      li.querySelector(".c-comp").textContent = disp.complaint || "";
      li.querySelector(".c-loc").textContent = disp.location || "";
      const waitMin = Math.max(0, Math.round((Date.parse(S.now) - Date.parse(q.arrived_at)) / 60000));
      li.querySelector(".c-arr").replaceChildren(document.createTextNode(hhmm(q.arrived_at)), el("span", { class: "sub" }, `${waitMin} min`));
      const nx = q.obligations[0];
      li.querySelector(".c-due").replaceChildren(...(nx ? [document.createTextNode(`${cap(nx.kind_text)}`),
        el("span", { class: "sub" }, `${hhmm(nx.due_at)} · ${nx.overdue ? `${-nx.minutes} min late` : `in ${nx.minutes} min`}`)] : [document.createTextNode("—")]));
      li.querySelector(".c-rn").textContent = (q.primary || "").split(",")[0];
      const tags = [];
      if (q.emergency_open) tags.push(el("span", { class: "tag tag-em" }, "Emerg?"));
      else if (q.emergency && q.emergency.startsWith("Emergency confirmed")) tags.push(el("span", { class: "tag tag-em" }, "Emergency"));
      if (overdue) tags.push(el("span", { class: "tag tag-od" }, "Overdue"));
      if (q.restricted) tags.push(el("span", { class: "tag tag-lock" }, "Restricted"));
      if (q.unreviewed_notes) tags.push(el("span", { class: "tag tag-note" }, `Note ×${q.unreviewed_notes}`));
      li.querySelector(".c-flags").replaceChildren(...tags);
    });
  if (removed) {
    $("queue-h").focus();
    say(`The encounter you were on was closed or is no longer available. Focus moved to the care queue heading.`);
  }
}

function renderInbox() {
  $("inbox-h").textContent = `Evidence inbox (${S.unreviewed} unreviewed)`;
  $("inbox-empty").hidden = S.inbox.length > 0;
  const h = $("ai-health");
  h.hidden = !S.ai_needs_manual;
  h.textContent = S.ai_needs_manual ? `${S.ai_needs_manual} note(s) need manual review because AI extraction did not complete. They stay in this inbox in arrival order.` : "";
  reconcile($("inbox"), S.inbox, (i) => i.item_id,
    (i) => {
      const li = el("li");
      const desc = el("span", { class: "desc cell", id: `id-${i.item_id}` });
      li.append(el("button", { type: "button", class: "open-note", "aria-describedby": desc.id, onclick: () => openItem(i.item_id) }),
        el("span", { class: "cell c-time", "aria-hidden": "true" }), el("span", { class: "cell c-ver", "aria-hidden": "true" }), desc);
      return li;
    },
    (li, i) => {
      const btn = li.querySelector("button");
      btn.textContent = i.label;
      btn.setAttribute("aria-label", `Open note review for ${i.label}, received ${hhmm(i.received_at)}`
        + (i.note_version > 1 ? ` (amended, version ${i.note_version})` : ""));
      li.querySelector(".c-time").textContent = `Recv ${hhmm(i.received_at)}`;
      li.querySelector(".c-ver").textContent = i.note_version > 1 ? `Amended v${i.note_version}` : "New note";
      const ai = i.ai_state;
      const cls = ai === "findings" || ai === "conflicting_evidence" ? "tag-ai" : (ai.startsWith("failed_") || ai === "malformed"
        || ai.startsWith("not_called") || ai.startsWith("blocked") || ai === "stale_discarded") ? "tag-od" : "tag-note";
      li.querySelector(".desc").replaceChildren(el("span", { class: `tag ${cls}` }, ai === "findings" ? "AI evidence" : ai.replace(/_/g, " ").slice(0, 22)),
        document.createTextNode(" " + i.ai_state_text + "."));
    });
}

function renderOverdue() {
  const body = $("ob-body");
  const rows = S.overdue;
  $("ob-h").textContent = `Overdue and owned actions (${rows.length} overdue)`;
  $("ob-caption").textContent = rows.length ? `${rows.length} overdue action(s), oldest first` : "No overdue actions";
  reconcile(body, rows, (o) => o.id,
    () => el("tr", {}, el("td"), el("td"), el("td"), el("td"), el("td"), el("td"), el("td"), el("td")),
    (tr, o) => {
      const c = tr.children;
      c[0].textContent = o.label; c[1].textContent = cap(o.kind_text); c[2].textContent = `${hhmm(o.due_at)}, ${mins(o.minutes)}`;
      c[3].textContent = o.owner; c[4].textContent = o.accepted_by ? `${o.accepted_by} at ${hhmm(o.accepted_at)}` : "Not accepted";
      c[5].textContent = o.reached_at ? hhmm(o.reached_at) : "Not recorded";
      c[6].textContent = o.escalated_to ? `${o.escalated_to} at ${hhmm(o.escalated_at)}` : "Not yet";
      if (!c[7].firstChild) c[7].append(obButtons(o));
      else c[7].replaceChildren(obButtons(o));
    });
}

function obButtons(o) {
  const wrap = el("div", { class: "btns" });
  if (!o.accepted_by) wrap.append(el("button", { type: "button", onclick: () => obAct(o.id, "accept") }, `Accept: ${o.kind_text}, ${o.label || ""}`.trim()));
  else if (!o.reached_at) wrap.append(el("button", { type: "button", onclick: () => obAct(o.id, "reached") }, `Mark patient reached`));
  wrap.append(el("button", { type: "button", onclick: () => obAct(o.id, "complete") }, `Mark complete`));
  return wrap;
}
async function obAct(id, action) {
  try { await api("POST", `/api/obligations/${id}/${action}`, {}); say(action === "accept" ? "Task accepted. The deadline is unchanged." : action === "reached" ? "Patient reached recorded." : "Action completed."); }
  catch (e) { say(`Could not update: ${e.message}`); }
  await refresh();
  if (openDetail && openDetail.kind === "encounter") openEncounter(openDetail.id, true);
}
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

// ---------------------------------------------------------------- detail panels
function showDetail(title, nodes, focus = true) {
  $("detail").hidden = false; $("app").classList.add("has-detail");
  if (S) renderQueue();
  $("detail-h").textContent = title;
  $("detail-body").replaceChildren(...nodes.filter((n) => n !== null && n !== undefined && n !== false));
  if (focus) $("detail-h").focus();
}
function backButton() {
  const label = openDetail && openDetail.label ? ` (${openDetail.label})` : "";
  return el("p", {}, el("button", { type: "button", onclick: backToQueue }, `Back to care queue${label}`));
}
function backToQueue() {
  const key = fromRowKey;
  $("detail").hidden = true; $("detail-body").replaceChildren(); openDetail = null;
  $("app").classList.remove("has-detail"); showView("patients", false); if (S) renderQueue();
  const row = key && [...$("queue").children].find((c) => c.dataset.key === key);
  if (row) row.querySelector(".open").focus(); else $("queue-h").focus();
}
function rememberCurrent(encounterId, label) {
  if (openDetail && openDetail.encounterId && openDetail.encounterId !== encounterId) {
    lastReviewed = { encounterId: openDetail.encounterId, label: openDetail.label };
    $("return-wrap").hidden = false;
    $("return-btn").textContent = `Return to ${lastReviewed.label} in care queue`;
  }
}

async function openEncounter(encId, keepFocus, tab = "summary") {
  const d = await getWithGlass(`/api/encounters/${encId}`, encId);
  if (!d) return;
  rememberCurrent(encId, d.label);
  openDetail = { kind: "encounter", id: encId, encounterId: encId, label: d.label, tab };
  fromRowKey = encId;
  document.title = `${d.label} chart, Triage Evidence Worklist`;
  const q = d.queue_row;
  const openFlags = d.emergencies.filter((f) => f.status === "awaiting_nurse" || f.status === "awaiting_charge");

  const disp = (q && q.display) || {};
  const header = el("div", { class: "chart-header" },
    el("div", { class: "chart-id" },
      el("span", { class: "pt" }, shownName(d.label)),
      d.restricted ? el("span", { class: "tag tag-lock" }, "Restricted record") : null,
      disp.age ? el("span", {}, `${disp.age} y · ${disp.sex}`) : null,
      el("span", { class: `lvl lvl-b-${q && q.priority ? q.priority : "u"}` }, q ? q.priority_text : d.status),
      disp.complaint ? el("span", {}, `CC: ${disp.complaint}`) : null,
      disp.location ? el("span", {}, disp.location) : null,
      el("span", {}, `Enc ${d.encounter_id}`),
      q ? el("span", {}, `RN: ${q.primary.split(",")[0]}`) : null),
    openFlags.length ? el("p", { class: "bad emerg-inline" }, el("strong", {}, "Possible emergency. "),
      `${openFlags[0].status_text}. `,
      el("button", { type: "button", onclick: () => openEmergency(openFlags[0].id) }, `Check ${d.label}'s possible emergency`)) : null);

  const summary = () => [
    el("p", {}, `Encounter ID ${d.encounter_id}. `, q ? `${q.priority_text}. Position ${q.position}. Primary: ${q.primary}.` : `Status: ${d.status}.`),
    el("h3", {}, "Open deadlines"),
    q && q.obligations.length ? el("ul", {}, q.obligations.map((o) => el("li", {},
      `${cap(o.kind_text)}: due ${hhmm(o.due_at)}, ${mins(o.minutes)}. Owner: ${o.owner}. ` +
      (o.accepted_by ? `Accepted by ${o.accepted_by}. ` : "Not accepted. ") + (o.reached_at ? `Reached ${hhmm(o.reached_at)}.` : ""),
      " ", obButtons(Object.assign({ label: d.label }, o))))) : el("p", {}, "No open deadlines."),
    el("h3", {}, `Unreviewed notes (${d.unreviewed_items.length})`),
    d.unreviewed_items.length ? el("ul", {}, d.unreviewed_items.map((i) => el("li", {},
      el("button", { type: "button", onclick: () => openItem(i.id) }, `Open note review, version ${i.note_version}`), ` ${i.ai_state_text}.`)))
      : el("p", {}, "None."),
    el("h3", {}, "Escalation and emergencies"),
    el("div", { class: "btns" },
      el("button", { type: "button", onclick: () => openEscalate(encId, d.label) }, `Escalate concern for ${d.label}`),
      el("button", { type: "button", class: "danger", onclick: () => openManualFlag(encId, d.label) }, `Flag ${d.label} as a possible emergency`)),
    el("p", { class: "hint" }, "Both are available at any time and never wait for the AI."),
    S.me.can_confirm_priority && q ? priorityForm({ encounter_id: d.encounter_id, label: d.label, priority: q.priority,
      encounter_version: d.version, item_id: null }) : null,
  ];

  // Epic-style chart: every note and document, newest first, searchable
  const allNotes = [
    ...d.notes.map((n) => ({ key: `${n.note_id}-${n.version}`, title: `Clinical note ${n.note_id} v${n.version}`, who: "Records feed (EHR)",
      when: n.signed_at, body: n.body, pin: { noteId: n.note_id, version: n.version } })),
    ...d.documents.map((x) => ({ key: x.id, title: x.note_type_text, who: x.author + (x.input_method === "dictated" ? " · dictated" : ""),
      when: x.created_at, body: x.body, pin: { noteId: x.note_type_text, version: hhmm(x.created_at) } })),
  ].sort((a, b) => (a.when < b.when ? 1 : -1));

  const notesTab = () => {
    const input = el("input", { type: "search", id: "chart-search", placeholder: "Search this patient's notes, e.g. denies",
      autocomplete: "off", "aria-describedby": "chart-search-count" });
    const count = el("p", { id: "chart-search-count", class: "hint", role: "status" });
    const list = el("div", { class: "doc-list" });
    const render = () => {
      const term = input.value.trim();
      const shown = term ? allNotes.filter((n) => (n.body + " " + n.title + " " + n.who).toLowerCase().includes(term.toLowerCase())) : allNotes;
      count.textContent = term ? `${shown.length} of ${allNotes.length} notes match "${term}".` : `${allNotes.length} notes and documents, newest first.`;
      list.replaceChildren(...shown.map((n) => el("article", { class: "doc", "aria-labelledby": `doc-${n.key}` },
        el("h4", { id: `doc-${n.key}` }, `${n.title} · ${n.who} · ${hhmm(n.when)}`),
        el("p", { class: "doc-body" }, ...highlight(n.body, term)),
        el("div", { class: "note-actions" }, el("button", { type: "button", onclick: () => pinNote({ label: d.label, encounterId: d.encounter_id,
          noteId: n.pin.noteId, version: n.pin.version, body: n.body, itemId: null }) }, `Pin ${n.title} (${hhmm(n.when)})`)))));
    };
    input.addEventListener("input", render);
    render();
    chartSearch = (t) => { input.value = t; render(); input.focus(); };
    return [el("label", { for: "chart-search", class: "search-label" }, `Search ${d.label}'s notes`),
      el("div", { class: "search-row" }, input), count, list];
  };

  const writeTab = () => {
    const type = el("select", { id: "doc-type" }, Object.entries(d.note_types).map(([k, v]) => el("option", { value: k }, v)));
    const ta = el("textarea", { id: "doc-body", rows: 7, placeholder: "Type the note here, or press Start dictation and speak." });
    if (dictationDraft[encId]) ta.value = dictationDraft[encId];
    ta.addEventListener("input", () => { dictationDraft[encId] = ta.value; });
    const status = el("p", { id: "dict-status", class: "hint", role: "status" });
    const dictBtn = el("button", { type: "button", id: "dict-btn", "aria-pressed": "false", onclick: () => toggleDictation(ta, dictBtn, status, encId) },
      "Start dictation");
    if (!SpeechRec) { dictBtn.disabled = true; status.textContent = "Dictation isn't available in this browser. Try Chrome or Safari, or type instead."; }
    const result = el("p", { id: "doc-result", tabindex: "-1" });
    const form = el("form", { onsubmit: async (ev) => {
      ev.preventDefault(); stopDictation();
      try {
        const r = await api("POST", `/api/encounters/${encId}/notes`, { body: ta.value, note_type: type.value,
          input_method: usedDictation[encId] ? "dictated" : "typed" });
        delete dictationDraft[encId]; delete usedDictation[encId];
        say(`Note saved to ${d.label}'s chart.` + (r.emergency_flags_raised ? " It matched an emergency rule, so a possible emergency was flagged." : ""));
        await refresh(); openEncounter(encId, false, "notes");
      } catch (e) { result.textContent = `Not saved: ${e.message}`; result.focus(); }
    } },
      el("p", {}, el("label", { for: "doc-type" }, "Note type "), type),
      el("label", { for: "doc-body" }, `Note for ${d.label}`), ta,
      el("div", { class: "btns" }, dictBtn, el("button", { type: "submit", class: "primary" }, "Save note to chart")),
      status, result);
    return [form, el("p", { class: "hint" }, speechPrivacyNote(),
      " Notes you write here stay on this server: they are never sent to the AI, and emergency rules check them automatically.")];
  };

  const emergTab = () => d.emergencies.length ? d.emergencies.map((f) => emergencyCard(f)) : [el("p", {}, "No emergency flags for this patient."),
    el("button", { type: "button", class: "danger", onclick: () => openManualFlag(encId, d.label) }, `Flag ${d.label} as a possible emergency`)];

  const tabs = [
    { id: "summary", label: "Summary", render: summary },
    { id: "notes", label: `Notes & documents (${allNotes.length})`, render: notesTab },
    { id: "write", label: "Write note", render: writeTab },
    { id: "emerg", label: `Emergencies (${d.emergencies.length})`, render: emergTab },
    { id: "access", label: "Access log", render: () => {
      const box = el("div", {}, el("p", { class: "hint" }, "Loading who has opened this chart…"));
      api("GET", `/api/encounters/${encId}/access-log`).then((rows) => box.replaceChildren(
        el("p", { class: "hint" }, "Every time someone opens this chart it is recorded here. Restricted records require a reason."),
        el("div", { class: "table-wrap" }, el("table", {}, el("caption", {}, `${rows.length} recent chart opens`),
          el("thead", {}, el("tr", {}, ...["Time", "Who", "What", "Reason"].map((h) => el("th", { scope: "col" }, h)))),
          el("tbody", {}, ...rows.map((r) => el("tr", {}, el("td", {}, hhmm(r.at)), el("td", {}, r.by),
            el("td", {}, r.break_glass ? `${r.action} (break-the-glass)` : r.action), el("td", {}, r.reason || "—")))))))
      ).catch(() => box.replaceChildren(el("p", {}, "Access log unavailable.")));
      return [box];
    } },
  ];
  const tabsEl = makeTabs(tabs, tab, encId);
  openDetail.selectTab = tabsEl.selectTab;
  showDetail(`${d.label}, encounter`, [header, tabsEl, backButton()], !keepFocus);
}

let chartSearch = null;
function makeTabs(tabs, initial, key) {
  const list = el("div", { role: "tablist", "aria-label": "Chart sections", class: "tabs" });
  const panel = el("div", { role: "tabpanel", id: `tabpanel-${key}`, tabindex: "0", class: "tabpanel" });
  const buttons = tabs.map((t) => el("button", { type: "button", role: "tab", id: `tab-${t.id}`, "aria-controls": panel.id,
    "aria-selected": "false", tabindex: "-1", onclick: () => select(t.id, true) }, t.label));
  function select(id, focus) {
    tabs.forEach((t, i) => { const on = t.id === id; buttons[i].setAttribute("aria-selected", String(on)); buttons[i].tabIndex = on ? 0 : -1; });
    const t = tabs.find((x) => x.id === id) || tabs[0];
    panel.setAttribute("aria-labelledby", `tab-${t.id}`);
    panel.replaceChildren(...t.render().flat().filter((n) => n !== null && n !== undefined && n !== false));
    if (openDetail) openDetail.tab = t.id;
    if (focus) buttons[tabs.indexOf(t)].focus();
  }
  list.addEventListener("keydown", (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (i < 0) return;
    let n = null;
    if (e.key === "ArrowRight") n = (i + 1) % buttons.length;
    if (e.key === "ArrowLeft") n = (i - 1 + buttons.length) % buttons.length;
    if (e.key === "Home") n = 0;
    if (e.key === "End") n = buttons.length - 1;
    if (n !== null) { e.preventDefault(); select(tabs[n].id, true); }
  });
  list.append(...buttons);
  select(initial, false);
  const wrap = el("div", { class: "tabs-wrap" }, list, panel);
  wrap.selectTab = select;
  return wrap;
}

function highlight(text, term) {
  if (!term) return [text];
  const out = []; const low = text.toLowerCase(); const t = term.toLowerCase();
  let i = 0, j;
  while ((j = low.indexOf(t, i)) >= 0) { out.push(text.slice(i, j), el("mark", {}, text.slice(j, j + t.length))); i = j + t.length; }
  out.push(text.slice(i));
  return out;
}

// ---------------------------------------------------------------- possible emergencies
let EM = null;
async function loadEmergencies() {
  try { EM = await api("GET", "/api/emergencies"); } catch (_) { return; }
  const open = EM.flags.filter((f) => f.status === "awaiting_nurse" || f.status === "awaiting_charge");
  $("nav-emerg").textContent = open.length || "";
  $("emerg-banner").hidden = !open.length;
  $("emerg-banner-text").textContent = open.length === 1 ? "1 possible emergency needs checking." : `${open.length} possible emergencies need checking.`;
  $("em-h").textContent = `Possible emergencies (${open.length} open)`;
  $("em-empty").hidden = EM.flags.length > 0;
  reconcile($("em-list"), EM.flags, (f) => f.id, () => el("li"), (li, f) => li.replaceChildren(emergencyCard(f, true)));
  $("em-rules").replaceChildren(...EM.rules.map((r) => el("li", {}, el("strong", {}, `${r.id}: ${r.label}. `), r.why)));
}

function emergencyCard(f, compact) {
  const open = f.status === "awaiting_nurse" || f.status === "awaiting_charge";
  const hid = `emh-${f.id}${compact ? "-c" : ""}`;
  return el("article", { class: `em-card ${open ? "em-open" : f.status === "confirmed_emergency" ? "em-confirmed" : "em-closed"}`,
    "aria-labelledby": hid },
    el("h3", { id: hid }, `${f.label}: ${f.factor}`),
    el("p", { class: "em-status" }, f.status_text + "."),
    f.evidence ? el("blockquote", {}, f.evidence) : null,
    el("p", { class: "hint" }, `Raised ${hhmm(f.raised_at)} by ${f.raised_by}` + (f.note_ref ? ` from ${f.note_ref}` : "") + "."),
    el("button", { type: "button", class: open ? "primary" : null, onclick: () => openEmergency(f.id) },
      open ? `Check ${f.label}'s possible emergency` : `View ${f.label}'s emergency decision`));
}

async function openEmergency(flagId) {
  await loadEmergencies();
  const f = EM && EM.flags.find((x) => x.id === flagId);
  if (!f) return;
  rememberCurrent(f.encounter_id, f.label);
  openDetail = { kind: "emergency", id: flagId, encounterId: f.encounter_id, label: f.label };
  fromRowKey = f.encounter_id;
  const nodes = [
    el("p", { class: f.status === "not_emergency" ? "warn" : "bad" }, el("strong", {}, f.status_text + ". "),
      "A flag does not change the care order. Nurses check it, and the charge nurse makes the final decision."),
    el("h3", {}, "Why it was flagged"),
    el("p", {}, f.source === "rule" ? `Rule ${f.rule_id}: ${f.factor}. ${f.why || ""}` : "A nurse flagged this patient. Their reason is in the history below."),
    f.evidence ? el("p", {}, el("strong", {}, "Exact sentence from the note:")) : null,
    f.evidence ? el("blockquote", {}, f.evidence) : null,
    el("p", {}, el("button", { type: "button", onclick: () => openEncounter(f.encounter_id, false, "notes") }, `Open ${f.label}'s chart and notes`)),
    el("h3", {}, "History"),
    el("ol", { class: "history" }, f.history.map((h) => el("li", {},
      el("strong", {}, `${hhmm(h.at)} · ${h.by}: `), historyText(h), h.reason ? el("span", {}, ` Reason: "${h.reason}"`) : null))),
    decisionBox(f, "assess"),
    EM.can_determine ? decisionBox(f, "determine") : el("p", { class: "hint" }, "The charge nurse sees your check and makes the final decision."),
    backButton(),
  ];
  showDetail(`Possible emergency: ${f.label}`, nodes);
}

function historyText(h) {
  if (h.action === "raised") return h.decision === "rule" ? "Flagged automatically by an emergency rule." : "Flagged by a nurse.";
  if (h.action === "nurse_assessment") return h.decision === "emergency" ? "Nurse check: this IS an emergency." : "Nurse check: NOT an emergency.";
  return h.decision === "confirmed_emergency" ? "Charge nurse decision: emergency confirmed (Level 1)." : "Charge nurse decision: not an emergency.";
}

function decisionBox(f, mode) {
  const nurse = mode === "assess";
  const name = `${mode}-${f.id}`;
  const opts = nurse ? [["emergency", "Yes, I think this is an emergency"], ["not_emergency", "No, I don't think this is an emergency"]]
    : [["confirmed_emergency", "Confirmed emergency (moves patient to Level 1)"], ["not_emergency", "Not an emergency"]];
  const radios = opts.map(([v, t]) => el("p", {}, el("input", { type: "radio", name, id: `${name}-${v}`, value: v }), " ",
    el("label", { for: `${name}-${v}` }, t)));
  const reasonId = `${name}-reason`;
  const ta = el("textarea", { id: reasonId, rows: 3, "aria-required": "true",
    placeholder: nurse ? "What did you see or hear? e.g. Saw the patient, breathing comfortably, symptom stopped." : "Why is this your decision?" });
  const result = el("p", { tabindex: "-1", class: "form-result" });
  return el("form", { class: nurse ? "card em-form" : "card em-form em-charge", onsubmit: async (ev) => {
    ev.preventDefault();
    const choice = ev.target.querySelector(`input[name="${name}"]:checked`);
    if (!choice) { result.textContent = "Please choose an answer first."; result.focus(); return; }
    if (ta.value.trim().length < 5) { result.textContent = "Please write your reason. It is required."; ta.focus(); return; }
    try {
      const r = await api("POST", `/api/emergency-flags/${f.id}/${mode}`, nurse
        ? { assessment: choice.value, reason: ta.value, expected_version: f.version }
        : { determination: choice.value, reason: ta.value, expected_version: f.version });
      say(nurse ? "Your check was saved. The charge nurse will see it." :
        (r.moved_to_level_1 ? `Emergency confirmed. ${f.label} moved to Level 1.` : "Your decision was saved."));
      await refresh(); openEmergency(f.id);
    } catch (e) { result.textContent = `Not saved: ${e.message}`; result.focus(); }
  } },
    el("fieldset", {}, el("legend", {}, nurse ? "Your check (any nurse)" : "Final decision (charge nurse)"), radios,
      el("label", { for: reasonId }, "Reason (required)"), ta),
    el("button", { type: "submit", class: "primary" }, nurse ? "Save my check" : "Save final decision"),
    result);
}

function openManualFlag(encId, label) {
  rememberCurrent(encId, label);
  openDetail = { kind: "flag", id: encId, encounterId: encId, label };
  fromRowKey = encId;
  const ta = el("textarea", { id: "flag-reason", rows: 3, placeholder: "What are you worried about?" });
  const result = el("p", { tabindex: "-1" });
  const form = el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    try { const r = await api("POST", `/api/encounters/${encId}/emergency-flag`, { reason: ta.value });
      say(`${label} was flagged as a possible emergency.`); await refresh(); openEmergency(r.flag_id); }
    catch (e) { result.textContent = `Not flagged: ${e.message}`; result.focus(); }
  } }, el("label", { for: "flag-reason" }, "Why might this be an emergency? (required)"), ta,
    el("button", { type: "submit", class: "primary" }, `Flag ${label} as a possible emergency`), result);
  showDetail(`Flag a possible emergency: ${label}`, [
    el("p", {}, "Any nurse can flag a patient. Other nurses will check it, and the charge nurse decides. "
      + "If someone needs help right now, also call for help the usual way."), form, backButton()]);
}

// ---------------------------------------------------------------- speech: dictation + voice commands
const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition || null;
const dictationDraft = {};
const usedDictation = {};
let activeRec = null;

function newRecognizer(continuous) {
  const rec = new SpeechRec();
  rec.lang = "en-US"; rec.continuous = continuous; rec.interimResults = true;
  if ("processLocally" in rec) { try { rec.processLocally = true; } catch (_) { /* not supported */ } }
  return rec;
}
function speechPrivacyNote() {
  return "Speech is turned into text by your browser. Some browsers send the audio to their own servers, "
    + "so in this demo never speak real patient information.";
}
function tidyDictation(t) {
  let s = " " + t.trim() + " ";
  s = s.replace(/\s+(period|full stop)\b/gi, ".").replace(/\s+comma\b/gi, ",").replace(/\s+question mark\b/gi, "?")
    .replace(/\s*new (line|paragraph)\s*/gi, "\n");
  s = s.trim();
  return s.replace(/(^|[.?!]\s+|\n)([a-z])/g, (m, p, c) => p + c.toUpperCase());
}
function stopDictation() { if (activeRec) { try { activeRec.stop(); } catch (_) { /* already stopped */ } activeRec = null; } }
function toggleDictation(ta, btn, status, encId) {
  if (activeRec) { stopDictation(); btn.setAttribute("aria-pressed", "false"); btn.textContent = "Start dictation"; status.textContent = "Dictation stopped."; return; }
  const rec = newRecognizer(true);
  let base = ta.value ? ta.value.replace(/\s*$/, " ") : "";
  rec.onresult = (e) => {
    let finalText = "", interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
    }
    if (finalText) { base = (base + tidyDictation(finalText) + " "); ta.value = base; dictationDraft[encId] = ta.value; usedDictation[encId] = true; }
    status.textContent = interim ? `Hearing: ${interim}` : "Listening. Say \"period\" or \"new line\" for punctuation.";
  };
  rec.onerror = (e) => { status.textContent = `Dictation stopped: ${e.error}. You can type instead.`; };
  rec.onend = () => { if (activeRec === rec) { activeRec = null; btn.setAttribute("aria-pressed", "false"); btn.textContent = "Start dictation"; } };
  activeRec = rec; rec.start();
  btn.setAttribute("aria-pressed", "true"); btn.textContent = "Stop dictation";
  status.textContent = "Listening. Speak your note. Say \"period\" or \"new line\" for punctuation.";
}

const LETTERS = { a: "A", ay: "A", eh: "A", b: "B", be: "B", bee: "B", c: "C", see: "C", sea: "C", d: "D", dee: "D", s: "S" };
function patientFrom(text) {
  const m = text.match(/patient\s+([a-z]+)/i);
  if (!m) return null;
  const letter = LETTERS[m[1].toLowerCase()] || (m[1].length === 1 ? m[1].toUpperCase() : null);
  return S && letter ? S.queue.find((q) => q.label === `Patient ${letter}`) : null;
}
const PAGES = [[/emergenc/, "emergencies"], [/notes? to review|inbox|new notes/, "inbox"], [/overdue/, "overdue"],
  [/practice|tools/, "tools"], [/help|words/, "help"], [/patients|queue|list/, "patients"]];

async function handleVoiceCommand(raw) {
  const text = raw.toLowerCase().replace(/[.,!?]/g, "").trim();
  const reply = (m) => { $("voice-result").textContent = m; say(m); return m; };
  if (!text) return reply("I didn't catch that.");
  const p = patientFrom(text);
  let m;
  if (/(take|write|start) (a )?(note|dictation)|dictate/.test(text)) {
    const target = p || (openDetail && S.queue.find((q) => q.encounter_id === openDetail.encounterId));
    if (!target) return reply("Say which patient, for example: take a note for patient A.");
    await openEncounter(target.encounter_id, false, "write");
    if (SpeechRec) setTimeout(() => $("dict-btn") && $("dict-btn").click(), 300);
    return reply(`Writing a note for ${target.label}.` + (SpeechRec ? " Dictation started." : ""));
  }
  if ((m = text.match(/(?:search|find|look) (?:for )?(.+?)(?: in patient [a-z]+)?$/))) {
    const target = p || (openDetail && S.queue.find((q) => q.encounter_id === openDetail.encounterId));
    if (!target) return reply("Open a patient first, or say: search for denies in patient B.");
    await openEncounter(target.encounter_id, false, "notes");
    chartSearch && chartSearch(m[1].replace(/^(?:for )/, "").replace(/ in patient [a-z]+$/, ""));
    return reply(`Searching ${target.label}'s notes for "${m[1]}".`);
  }
  if (/escalate/.test(text) && p) { openEscalate(p.encounter_id, p.label); return reply(`Opening escalation for ${p.label}.`); }
  if (/(flag|emergency)/.test(text) && p) { openManualFlag(p.encounter_id, p.label); return reply(`Opening an emergency flag for ${p.label}.`); }
  if (p && /(bring up|open|show|pull up|go to|chart|patient)/.test(text)) {
    await openEncounter(p.encounter_id);
    return reply(`Opened ${p.label}'s chart.`);
  }
  for (const [rx, view] of PAGES) if (rx.test(text)) { showView(view); return reply(`Opened ${view === "inbox" ? "notes to review" : view}.`); }
  if (/close|back/.test(text)) { backToQueue(); return reply("Closed the panel."); }
  return reply(`I heard "${raw}", but didn't recognize a command. Try: bring up patient A.`);
}
window.triageVoice = { handle: handleVoiceCommand };

function listenForCommand() {
  if (!SpeechRec) { $("voice-result").textContent = "Voice isn't available in this browser. Type a command instead."; $("voice-text").focus(); return; }
  const rec = newRecognizer(false);
  const btn = $("voice-listen");
  btn.setAttribute("aria-pressed", "true"); btn.textContent = "Listening…";
  $("voice-result").textContent = "Listening. Say a command, like: bring up patient A.";
  rec.onresult = (e) => {
    const r = e.results[e.results.length - 1];
    $("voice-text").value = r[0].transcript;
    if (r.isFinal) handleVoiceCommand(r[0].transcript);
  };
  rec.onerror = (e) => { $("voice-result").textContent = `Voice stopped: ${e.error}. You can type the command instead.`; };
  rec.onend = () => { btn.setAttribute("aria-pressed", "false"); btn.textContent = "Start listening"; };
  rec.start();
}

function openEscalate(encId, label) {
  rememberCurrent(encId, label);
  openDetail = { kind: "escalate", id: encId, encounterId: encId, label };
  fromRowKey = encId;
  const sel = el("select", { id: "esc-reason" }, (S.escalation_reasons || []).map((r) => el("option", { value: r }, cap(r.replace(/_/g, " ")))));
  const form = el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    try { await api("POST", `/api/encounters/${encId}/escalate`, { reason_code: sel.value });
      $("esc-result").textContent = `Escalation recorded for ${label}. The charge nurse has a 5-minute response deadline (illustrative).`;
      $("esc-result").focus(); await refresh(); }
    catch (e) { $("esc-result").textContent = `Escalation failed: ${e.message}. Use the facility's phone or overhead escalation now.`; $("esc-result").focus(); }
  } },
    el("label", { for: "esc-reason" }, "Reason"), " ", sel, " ",
    el("button", { type: "submit", class: "primary" }, `Raise escalation for ${label}`));
  showDetail(`Escalate concern for ${label}`, [
    el("p", {}, "Manual escalation never waits for AI and does not depend on it. It creates a tracked response deadline for the charge nurse."),
    form, el("p", { id: "esc-result", tabindex: "-1" }), backButton()]);
}

async function openItem(itemId) {
  const d = await getWithGlass(`/api/review-items/${itemId}`, null);
  if (!d) return;
  rememberCurrent(d.encounter_id, d.label);
  openDetail = { kind: "item", id: itemId, encounterId: d.encounter_id, label: d.label, state: d.state, submitted: false };
  if (!fromRowKey || (openDetail && openDetail.encounterId !== fromRowKey)) fromRowKey = d.encounter_id;
  document.title = `Note review, ${d.label}, Triage Evidence Worklist`;
  const draft = drafts[itemId] || (drafts[itemId] = { decision: "", comment: "", corrections: {} });
  if (d.encounter_id === "enc_B" && d.note.note_id === "note_B2") { tour.opened = true; saveTour(); renderGuide(); }
  const nodes = [];
  nodes.push(el("p", { class: "warn" }, el("strong", {}, d.label_ai), ` Status: ${d.ai_state_text}.`));
  if (!document.body.classList.contains("no-hints")) nodes.push(el("div", { class: "howto" },
    el("p", {}, el("strong", {}, "How to check this note, in 3 steps:")),
    el("ol", {},
      el("li", {}, "Read each finding below. The grey box shows the exact words the AI found."),
      el("li", {}, "Open \"Original note, full text\" and make sure the AI understood it. Yellow boxes point out things to double-check."),
      el("li", {}, "At the bottom, choose Accept, Correct, or Reject, then press \"Record review decision\". Your choice is saved with your name."))));
  nodes.push(el("p", {}, `Encounter ID ${d.encounter_id}. Note ${d.note.note_id} version ${d.note.version}, signed ${hhmm(d.note.signed_at)}, received ${hhmm(d.note.received_at)}. Current confirmed priority: ${d.priority ? "Level " + d.priority : "unassessed"}. AI output never changes this.`));
  nodes.push(el("details", {}, el("summary", {}, "Original note, full text"), el("p", {}, d.note.body)));
  nodes.push(el("div", { class: "note-actions" }, el("button", { type: "button", onclick: () => pinNote({ label: d.label,
    encounterId: d.encounter_id, noteId: d.note.note_id, version: d.note.version, body: d.note.body, itemId: d.item_id }) },
    `Pin this note (${d.label})`)));
  if (d.state !== "unreviewed") nodes.push(el("p", { class: "bad" }, d.state === "superseded" ? "This version was amended. Review the newer version in the evidence inbox." : "This note has been reviewed."));

  const usable = d.findings.filter((f) => !f.checks.rejected);
  const rejected = d.findings.filter((f) => f.checks.rejected);
  nodes.push(el("h3", {}, `Candidate findings (${usable.length})`));
  if (!usable.length) nodes.push(el("p", {}, "No AI evidence to show. Read the original note above."));
  usable.forEach((f, i) => nodes.push(findingCard(f, i, draft, itemId)));
  if (rejected.length) nodes.push(el("p", { class: "bad" }, `${rejected.length} AI item(s) were not shown because their quotes did not match the note.`));
  if (d.reviewer_note) nodes.push(el("p", {}, el("strong", {}, "AI note to reviewer (unverified): "), d.reviewer_note));

  if (d.state === "unreviewed") nodes.push(decisionForm(d, draft, usable));
  if (S.me.can_confirm_priority && d.state !== "superseded") nodes.push(priorityForm(d));
  else if (!S.me.can_confirm_priority) nodes.push(el("p", {}, "Priority changes require a charge RN under the demo protocol. If you are concerned now, use Escalate concern."),
    el("p", {}, el("button", { type: "button", onclick: () => openEscalate(d.encounter_id, d.label) }, `Escalate concern for ${d.label}`)));
  if (d.decisions.length) nodes.push(el("h3", {}, "Review history"), el("ul", {}, d.decisions.map((x) => el("li", {},
    `${x.by} at ${hhmm(x.at)}: ${x.decision}` + (Object.keys(x.corrections).length ? `, corrected ${JSON.stringify(x.corrections)}` : "") + (x.comment ? `. Comment: ${x.comment}` : "")))));
  nodes.push(whatClaudeReceived(itemId));
  if (d.model_call) nodes.push(el("p", { class: "rule" }, `Processing record: mode ${d.model_call.mode}, ${describeProvenance(d.model_call.provenance, d.model_call.outcome)}, model ${d.model_call.model}, prompt ${d.model_call.prompt_version}, schema ${d.model_call.schema_version}, selector ${d.model_call.selector_version}.`));
  nodes.push(backButton());
  showDetail(`Note review: ${d.label} (AI-assisted, unverified)`, nodes);
}

const LABELS = {
  subject: { patient: "the patient", family_member: "a family member", other_person: "another person", unclear: "unclear" },
  assertion: { reported: "reported", denied: "denied", uncertain: "uncertain" },
  time_context: { current: "current", past_resolved: "in the past, resolved", past_unspecified: "in the past", unclear: "unclear" },
  change: { newly_reported: "newly reported", denied_then_reported: "earlier denied, now reported", reported_then_denied: "earlier reported, now denied", unchanged: "unchanged", not_comparable: "no valid comparison" },
};

function findingCard(f, i, draft, itemId) {
  const c = f.checks;
  const corr = draft.corrections[f.id] || (draft.corrections[f.id] = {});
  const sel = (field, val) => {
    const id = `c-${f.id}-${field}`;
    const s = el("select", { id, onchange: (e) => { if (e.target.value === val) delete corr[field]; else corr[field] = e.target.value; } },
      Object.entries(LABELS[field]).map(([k, v]) => el("option", { value: k, selected: (corr[field] || val) === k }, v)));
    return el("p", {}, el("label", { for: id }, `Correct ${field.replace("_", " ")}: `), s);
  };
  const cmpText = f.comparison.change === "not_comparable" ? "No valid comparison with an earlier note." : `${cap(LABELS.change[f.comparison.change])}.`;
  return el("article", { class: "finding", "aria-labelledby": `fh-${f.id}` },
    el("h4", { id: `fh-${f.id}` }, `Finding ${i + 1}: ${f.finding_label}, ${LABELS.assertion[f.assertion]}, about ${LABELS.subject[f.subject]}, ${LABELS.time_context[f.time_context]}`),
    el("p", {}, el("strong", {}, "Exact quote from note: ")), el("blockquote", {}, f.quote),
    c.full_sentence && c.full_sentence !== f.quote ? [el("p", {}, el("strong", {}, "Full sentence in the note: ")), el("blockquote", {}, c.full_sentence)] : null,
    el("dl", {},
      el("dt", {}, "Change"), el("dd", {}, cmpText),
      f.comparison.prior_quote ? [el("dt", {}, "Earlier wording"), el("dd", {}, f.comparison.prior_quote + (c.prior_note ? ` (note ${c.prior_note.note_id} v${c.prior_note.version})` : ""))] : null,
      f.time_evidence_quote ? [el("dt", {}, "Time wording"), el("dd", {}, f.time_evidence_quote)] : null,
      f.uncertainty_reason ? [el("dt", {}, "Ambiguity"), el("dd", {}, f.uncertainty_reason)] : null,
      el("dt", {}, "Source"), el("dd", {}, c.source_role === "current_note_excerpt" ? "This note" : "Earlier note in this encounter")),
    c.warnings.length ? el("div", { class: "warn" }, el("p", {}, el("strong", {}, `Context checks (${c.warnings.length}):`)), el("ul", {}, c.warnings.map((w) => el("li", {}, w)))) : null,
    el("details", {}, el("summary", {}, `Correct finding ${i + 1}`),
      sel("subject", f.subject), sel("assertion", f.assertion), sel("time_context", f.time_context), sel("change", f.comparison.change)));
}

function decisionForm(d, draft, usable) {
  const opts = usable.length
    ? [["accept", "Accept AI interpretation as shown"], ["correct", "Accept with my corrections"], ["reject", "Reject AI interpretation"]]
    : [["reviewed_no_ai", "I reviewed the original note (no AI evidence)"]];
  const radios = opts.map(([v, t]) => el("p", {}, el("input", { type: "radio", name: "decision", id: `dec-${v}`, value: v, checked: draft.decision === v,
    onchange: () => { draft.decision = v; } }), " ", el("label", { for: `dec-${v}` }, t)));
  const ta = el("textarea", { id: "dec-comment", oninput: (e) => { draft.comment = e.target.value; } });
  ta.value = draft.comment;
  const result = el("p", { id: "dec-result", tabindex: "-1" });
  return el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    if (!draft.decision) { result.textContent = "Choose a review decision first."; result.focus(); return; }
    const corrections = {};
    for (const [fid, fields] of Object.entries(draft.corrections)) if (Object.keys(fields).length) corrections[fid] = fields;
    const decision = draft.decision === "accept" && Object.keys(corrections).length ? "correct" : draft.decision;
    try {
      await api("POST", `/api/review-items/${d.item_id}/decision`, { decision, corrections, comment: draft.comment,
        expected_version: d.version, disclosed: !!draft.disclosed });
      openDetail.submitted = true; delete drafts[d.item_id];
      if (d.encounter_id === "enc_B") { tour.decided = true; saveTour(); }
      result.textContent = S.me.can_confirm_priority
        ? "Review recorded. The care order has not changed. If the patient needs a new level, use the form below."
        : "Review recorded. The care order has not changed. Next step: see \"What to do next\" at the top of the page."; result.focus();
      await refresh();
    } catch (e) { result.textContent = `Not recorded: ${e.message}. Your draft is kept.`; result.focus(); }
  } },
    el("fieldset", {}, el("legend", {}, "Your review of this note"), radios,
      el("label", { for: "dec-comment" }, "Comment (optional)"), ta,
      el("p", {}, el("input", { type: "checkbox", id: "dec-disclosed", checked: !!draft.disclosed,
        onchange: (e) => { draft.disclosed = e.target.checked; } }), " ",
        el("label", { for: "dec-disclosed" }, "I told the patient that AI assisted with this review"))),
    el("button", { type: "submit", class: "primary" }, "Record review decision"), result);
}

function priorityForm(d) {
  const lvl = el("select", { id: "pr-level" }, [1, 2, 3, 4].map((n) => el("option", { value: n, selected: n === d.priority }, `Level ${n}`)));
  const reason = el("select", { id: "pr-reason" }, [["new_evidence_confirmed", "New evidence verified in note"], ["clinical_judgement", "Clinical judgement"],
    ["protocol_criteria_met", "Protocol criteria met"], ["correction_of_error", "Correcting an earlier error"]].map(([v, t]) => el("option", { value: v }, t)));
  const result = el("p", { id: "pr-result", tabindex: "-1" });
  return el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    try {
      await api("POST", `/api/encounters/${d.encounter_id}/priority`, { priority: Number(lvl.value), reason_code: reason.value,
        expected_version: d.encounter_version, review_item_id: d.item_id });
      result.textContent = `Priority for ${d.label} confirmed as Level ${lvl.value}. The care queue was reordered.`; result.focus();
      await refresh();
    } catch (e) { result.textContent = `Not changed: ${e.message}`; result.focus(); }
  } },
    el("fieldset", {}, el("legend", {}, `Confirm official priority for ${d.label} (qualified clinician, facility protocol)`),
      el("p", {}, `Current: ${d.priority ? "Level " + d.priority : "unassessed"}. Only this action changes the care order.`),
      el("label", { for: "pr-level" }, "New level "), lvl, " ", el("label", { for: "pr-reason" }, "Reason "), reason),
    el("button", { type: "submit", class: "primary" }, "Confirm priority"), result);
}

function whatClaudeReceived(itemId) {
  const box = el("div");
  const det = el("details", { ontoggle: async () => {
    if (!det.open || box.childElementCount) return;
    try {
      const r = await getWithGlass(`/api/review-items/${itemId}/ai-request`, null);
      box.append(
        el("p", {}, `Processing mode: ${r.mode}. ${describeProvenance(r.provenance, r.outcome)}. Model setting: ${r.model}. Endpoint: ${r.endpoint}.`),
        el("h4", {}, "Exact outbound excerpts"),
        el("ul", {}, r.outbound_user_content.excerpts.map((x) => el("li", {}, `${x.alias} (${x.role.replace(/_/g, " ")}, signed ${x.signed}): `, el("q", {}, x.text)))),
        el("p", {}, `Watched terms sent: ${r.outbound_user_content.watched_terms.join(", ")}.`),
        el("h4", {}, "Not sent"), el("ul", {}, r.omitted_fields.map((x) => el("li", {}, x))),
        el("h4", {}, "Other request fields"), el("pre", { class: "wire" }, JSON.stringify(r.outbound_other_fields, null, 2)),
        el("h4", {}, "Returned text (before validation)"), el("pre", { class: "wire" }, r.returned_text || "(none)"));
    } catch (e) { box.append(el("p", {}, "Nothing was sent to Claude for this note (or it is not an approved demo fixture).")); }
  } }, el("summary", {}, "What Claude received (synthetic demo fixture)"), box);
  return det;
}

async function checkOpenDetail() {
  if (!openDetail || openDetail.kind !== "item" || openDetail.submitted) return;
  if (S.inbox.some((i) => i.item_id === openDetail.id)) return;
  const body = $("detail-body");
  if (body.querySelector(".changed-banner")) return;
  body.prepend(el("p", { class: "bad changed-banner" }, "This note was reviewed by someone else or amended while you had it open. Your draft is kept. Check the evidence inbox for a newer version."));
  say("The note you have open changed. Your draft is kept.");
}

// ---------------------------------------------------------------- demo controls
async function loadFixtures() {
  const wrap = $("deliver-buttons"); wrap.replaceChildren();
  let fx = [];
  try { fx = await api("GET", "/api/sim/fixtures"); } catch (_) { return; }
  for (const f of fx) {
    const key = `${f.note_id}:${f.version}`;
    const first = key === "note_B2:1";
    wrap.append(el("button", { type: "button", class: first ? "primary" : null, onclick: () => deliver(f.note_id, f.version) },
      NOTE_HELP[key] || `Send note for ${f.label}: ${f.case}`));
  }
}

async function deliver(noteId, version) {
  try {
    const r = await api("POST", "/api/sim/deliver", { note_id: noteId, version });
    if (r && r.delivered === false) { say("The records feed is stopped, so the note was not delivered. Restart the feed in More practice tools."); return; }
    if (r && r.duplicate) { say("That note was already sent. Nothing changed."); return; }
    if (noteId === "note_B2" && version === 1) { tour.sent = true; saveTour(); }
    $("latest-text").textContent = "Note sent. Look in the evidence inbox. The AI helper is reading it.";
    renderGuide();
  } catch (e) { say(`Could not send the note: ${e.message}`); }
}

// ---------------------------------------------------------------- beginner guide
const USER_HELP = {
  u_riley: "Nurse. Reads new notes and checks the AI's suggestions. Best place to start.",
  u_sam: "Charge nurse. Can approve a change in how urgent a patient is. Use in a second window.",
  u_jordan: "Nurse who is on a break. Sam covers Jordan's patients.",
  u_casey: "Nurse at a different clinic. Cannot see this clinic's patients, which shows the privacy protection.",
  u_drlee: "Resident physician. Can review evidence and confirm priority changes.",
  u_morgan: "Clinical instructor. Builds scenarios, approves student cases, sees class results.",
  u_taylor: "Nursing student. Practices scenarios and builds their own cases.",
  u_avery: "Medical student. Practices scenarios and builds their own cases.",
  u_alex: "Unit administrator. Creates staff accounts.",
};
const NOTE_HELP = {
  "note_B2:1": "Start here: send a new note for Patient B (a symptom they denied before is now reported)",
  "note_B2:2": "Send a correction to Patient B's note (the earlier entry was a mistake)",
  "note_A2:1": "Send a note for Patient A with a sneaky instruction inside (the AI should ignore it)",
  "note_A3:1": "Send a note for Patient A where the AI makes a mistake (see if you catch it)",
  "note_C2:1": "Send a note for Patient C about something that went away last week",
  "note_C3:1": "Send a note for Patient C that copies an earlier sentence",
  "note_D1:1": "Send a note for Patient D with a warning sign (a possible emergency)",
};

let tour = loadTour();
function loadTour() {
  try { return Object.assign({ sent: false, opened: false, decided: false, hidden: false }, JSON.parse(localStorage.getItem("tour") || "{}")); }
  catch (_) { return { sent: false, opened: false, decided: false, hidden: false }; }
}
function saveTour() { try { localStorage.setItem("tour", JSON.stringify(tour)); } catch (_) { /* storage unavailable */ } }

function renderGuide() {
  if (!S) return;
  const bItem = S.inbox.find((i) => i.encounter_id === "enc_B");
  const bRow = S.queue.find((q) => q.encounter_id === "enc_B");
  const confirmed = bRow && bRow.priority === 1;
  if (bItem || confirmed) tour.sent = true;
  const step = $("guide-step"), actions = $("guide-actions"), prog = $("guide-progress");
  actions.replaceChildren();
  let n, text;
  const btn = (label, fn, primary) => actions.append(el("button", { type: "button", class: primary ? "primary" : null, onclick: fn }, label));
  if (confirmed) {
    n = 5;
    text = "All done. Patient B moved to the top because a charge nurse approved it. Patient A's check-up time did not change. " +
      "If you are ever worried about a patient, press \"Escalate concern\". It never waits for the AI. " +
      "Want to explore? Try the other practice notes, or make the AI have a problem in More practice tools.";
    btn("Go to the care queue", () => showView("patients"), true);
  } else if (!tour.sent) {
    n = 1;
    text = "Look at the care queue below. The top patient is seen first. When you are ready, send a new practice note for Patient B and watch what happens.";
    btn("Send Patient B's new note", () => deliver("note_B2", 1), true);
    btn("Show me the care queue", () => showView("patients"));
  } else if (!tour.decided && bItem) {
    if (!tour.opened) {
      n = 2;
      text = bItem.ai_state === "queued" || bItem.ai_state === "in_progress"
        ? "A new note arrived. The AI helper is reading it now. This takes a moment. You can open it already."
        : "A new note arrived for Patient B. Open it to see what the AI helper found.";
      btn("Open Patient B's new note", () => openItem(bItem.item_id), true);
      btn("Go to the notes to review", () => showView("inbox"));
    } else {
      n = 3;
      text = "Read the AI's suggestion. Compare the quote with the full note. Then choose Accept, Correct, or Reject, and press \"Record review decision\".";
      btn("Open Patient B's note again", () => openItem(bItem.item_id), true);
    }
  } else {
    n = 4;
    text = S.me.can_confirm_priority
      ? "You are the charge nurse. Open Patient B's note, choose Level 1, and press \"Confirm priority\". Patient B will move to the top of the queue."
      : "Only a charge nurse can change how urgent Patient B is. Open a second browser window, go to this same page, and sign in as Sam. As Sam, open Patient B's note, choose Level 1, and press \"Confirm priority\". Then come back here: Patient B moves up, and your place on Patient A stays put.";
    const anyB = S.inbox.find((i) => i.encounter_id === "enc_B");
    if (S.me.can_confirm_priority) btn(anyB ? "Open Patient B's note" : "Open Patient B", () => (anyB ? openItem(anyB.item_id) : openPriorityFor("enc_B")), true);
    btn("Show me the care queue", () => showView("patients"), !S.me.can_confirm_priority);
  }
  prog.textContent = `Step ${n} of 5`;
  step.textContent = text;
  applyHidden();
}

async function openPriorityFor(encId) {
  // Charge nurse may confirm without an open note: open the latest reviewed item for the patient if any, else the encounter.
  await openEncounter(encId);
}

function applyHidden() {
  const hidden = !!tour.hidden;
  document.body.classList.toggle("no-hints", hidden);
  for (const id of ["guide-progress", "guide-step", "guide-actions"]) $(id).hidden = hidden;
  $("guide-toggle").textContent = hidden ? "Show tips" : "Hide tips";
  $("guide-toggle").setAttribute("aria-expanded", String(!hidden));
}

function setTextSize(scale) {
  document.documentElement.style.fontSize = `${Math.round(scale * 93.75)}%`;
  document.querySelectorAll("[data-size]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.size) === scale)));
  try { localStorage.setItem("textsize", String(scale)); } catch (_) { /* ignore */ }
}
// ---------------------------------------------------------------- pages (views)
const VIEW_HEADINGS = { patients: "queue-h", emergencies: "em-h", inbox: "inbox-h", overdue: "ob-h", tools: "demo-h", help: "help-h",
  privacy: "priv-h" };
let currentView = "patients";
function showView(name, focus = true) {
  currentView = name;
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== name; });
  document.querySelectorAll(".nav").forEach((b) => {
    if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  if (name === "privacy") loadPrivacy();
  if (focus) $(VIEW_HEADINGS[name]).focus();
}

// ---------------------------------------------------------------- pinned sticky notes
const pins = [];
function pinNote(p) {
  if (pins.some((x) => x.noteId === p.noteId && x.version === p.version)) { say("That note is already pinned."); return; }
  if (pins.length >= 4) pins.shift();
  pins.push(p); renderPins();
  say(`Note pinned for ${p.label}. Pinned notes stay on screen while you work.`);
}
function renderPins() {
  const list = $("pins-list");
  list.replaceChildren(...pins.map((p, i) => el("article", { class: "pin", "aria-labelledby": `pin-h-${i}` },
    el("h3", { id: `pin-h-${i}` }, `${p.label} · ${p.noteId} v${p.version}`),
    el("p", {}, p.body),
    el("div", { class: "btns" },
      el("button", { type: "button", onclick: () => (p.itemId ? openItem(p.itemId) : openEncounter(p.encounterId)) },
        p.itemId ? "Open review" : "Open chart"),
      el("button", { type: "button", onclick: () => { pins.splice(pins.indexOf(p), 1); renderPins(); say("Note unpinned."); } },
        `Unpin ${p.label} note`)))));
  $("pins").hidden = pins.length === 0;
  const open = $("pins-toggle").getAttribute("aria-expanded") === "true";
  $("pins-toggle").textContent = `Pinned notes (${pins.length}) · ${open ? "Hide" : "Show"}`;
  $("app").classList.toggle("has-pins", pins.length > 0);
}

function simBind() {
  document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $("detail-close").addEventListener("click", backToQueue);
  $("emerg-banner-btn").addEventListener("click", () => showView("emergencies"));
  $("voice-btn").addEventListener("click", () => {
    const open = $("voice-panel").hidden;
    $("voice-panel").hidden = !open; $("voice-btn").setAttribute("aria-expanded", String(open));
    if (open) $("voice-listen").focus();
  });
  $("voice-listen").addEventListener("click", listenForCommand);
  $("voice-form").addEventListener("submit", (e) => { e.preventDefault(); handleVoiceCommand($("voice-text").value); });
  $("voice-close").addEventListener("click", () => { $("voice-panel").hidden = true; $("voice-btn").setAttribute("aria-expanded", "false"); $("voice-btn").focus(); });
  $("voice-privacy").textContent = speechPrivacyNote();
  if (!SpeechRec) { $("voice-listen").disabled = true; $("voice-result").textContent = "Speaking isn't available in this browser, but you can type a command."; }
  $("pins-toggle").addEventListener("click", () => {
    const open = $("pins-toggle").getAttribute("aria-expanded") !== "true";
    $("pins-toggle").setAttribute("aria-expanded", String(open));
    $("pins-list").hidden = !open; renderPins();
  });
  document.querySelectorAll("[data-size]").forEach((b) => b.addEventListener("click", () => setTextSize(Number(b.dataset.size))));
  try { const s = Number(localStorage.getItem("textsize")); if (s) setTextSize(s); } catch (_) { /* ignore */ }
  $("guide-toggle").addEventListener("click", () => { tour.hidden = !tour.hidden; saveTour(); applyHidden(); });
  $("guide-restart").addEventListener("click", () => {
    tour = { sent: false, opened: false, decided: false, hidden: false }; saveTour(); renderGuide();
    $("guide-h").focus();
  });
  document.querySelectorAll("[data-sim-clock]").forEach((b) => b.addEventListener("click", async () => {
    await api("POST", "/api/sim/clock", { minutes: Number(b.dataset.simClock) }); refresh(); }));
  $("fault-apply").addEventListener("click", async () => { await api("POST", "/api/sim/provider", { fault: $("fault").value }); refresh(); });
  $("feed-stall").addEventListener("click", () => api("POST", "/api/sim/feed", { stalled: true }));
  $("feed-resume").addEventListener("click", () => api("POST", "/api/sim/feed", { stalled: false }));
  $("close-a").addEventListener("click", () => api("POST", "/api/sim/close", { encounter_id: "enc_A" }));
  $("revoke-me").addEventListener("click", () => api("POST", "/api/sim/revoke", { user_id: S.me.id }));
  $("reset").addEventListener("click", async () => {
    await api("POST", "/api/sim/reset", {});
    tour = { sent: false, opened: false, decided: false, hidden: tour.hidden }; saveTour();
    location.reload();
  });
  $("logout").addEventListener("click", async () => {
    await api("POST", "/api/logout", {}); pins.length = 0; renderPins(); $("app").classList.remove("has-detail"); $("detail").hidden = true; showLogin(); });
  $("return-btn").addEventListener("click", () => {
    if (!lastReviewed) return;
    const row = [...$("queue").children].find((c) => c.dataset.key === lastReviewed.encounterId);
    if (row) row.querySelector(".open").focus(); else say(`${lastReviewed.label} is no longer in the care queue.`);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  simBind();
  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    $("login-error").textContent = "";
    login({ username: $("login-user").value.trim(), password: $("login-pass").value });
  });
  try { const me = await api("GET", "/api/me"); if (me.home && me.home !== "/") { location.href = me.home; return; } }
  catch (_) { return; }   // not signed in: showLogin() already ran
  try { await refresh(); if (S) { $("app").hidden = false; connect(); loadFixtures(); } }
  catch (_) { /* showLogin already called on 401 */ }
  setInterval(() => { if (S && connState !== "down") refresh(); }, 30000);
});

// ---------------------------------------------------------------- privacy: names, lock, break-the-glass, dashboard
let hideNames = false;
function shownName(label) {
  if (!hideNames || !label) return label;
  return label.split(/\s+/).map((w) => w[0].toUpperCase() + ".").join("");
}

function askReason(title, text) {
  return new Promise((resolve) => {
    const prev = document.activeElement;
    const ta = el("textarea", { id: "glass-reason", rows: 3 });
    const err = el("p", { class: "form-result", role: "alert" });
    const close = (v) => { back.remove(); $("app").inert = false; if (prev && prev.focus) prev.focus(); resolve(v); };
    const form = el("form", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "glass-h",
      onsubmit: (e) => { e.preventDefault(); if (ta.value.trim().length < 5) { err.textContent = "Please write a reason."; ta.focus(); return; } close(ta.value.trim()); } },
      el("h2", { id: "glass-h", tabindex: "-1" }, title), el("p", {}, text),
      el("label", { for: "glass-reason" }, "Reason (recorded and reviewed by the privacy officer)"), ta, err,
      el("div", { class: "btns" }, el("button", { type: "submit", class: "primary" }, "Open record"),
        el("button", { type: "button", onclick: () => close(null) }, "Cancel")));
    form.addEventListener("keydown", (e) => { if (e.key === "Escape") close(null); });
    const back = el("div", { class: "modal-back" }, form);
    document.body.append(back);
    $("app").inert = true;
    ta.focus();
  });
}

async function getWithGlass(path) {
  try { return await api("GET", path); }
  catch (e) {
    if (e.message !== "break_glass_required") throw e;
    const reason = await askReason("Restricted record",
      "This patient's record is restricted. You're not on their care team, so opening it needs a reason. This access will be logged.");
    if (!reason) { say("Record not opened."); return null; }
    return api("GET", `${path}${path.includes("?") ? "&" : "?"}reason=${encodeURIComponent(reason)}`);
  }
}

async function loadPrivacy() {
  let p;
  try { p = await api("GET", "/api/privacy"); } catch (_) { return; }
  const stat = (n, l, cls) => el("div", { class: `stat ${cls || ""}` }, el("div", { class: "n" }, String(n)), el("div", { class: "l" }, l));
  const leaks = p.identifiers_found_in_ai_requests.length;
  $("priv-stats").replaceChildren(
    stat(p.ai_requests, "Requests sent to the AI"),
    stat(leaks, leaks ? `Identifier types found in AI requests: ${p.identifiers_found_in_ai_requests.join(", ")}` : "Identifiers found in AI requests", leaks ? "bad" : "good"),
    stat(p.scrubbed_tokens, "Codes, links or numbers scrubbed before sending"),
    stat(p.typed_or_dictated_sent_to_ai, `Typed or dictated notes sent to the AI (of ${p.typed_or_dictated_notes})`, "good"),
    stat(p.chart_opens_logged, "Chart opens logged"),
    stat(p.break_glass_count, "Break-the-glass openings"),
    stat(p.ai_requests_purged, `AI records purged (kept ${p.retention_days} days)`),
    stat(p.encryption_at_rest ? "On" : "Off", "Encryption of note text at rest", p.encryption_at_rest ? "good" : ""));
  $("priv-facts").replaceChildren(
    el("li", {}, `Checked for: ${p.identifier_types_checked.join(", ")}.`),
    el("li", {}, "Only short excerpts of approved notes are ever sent, with names, record numbers and dates removed."),
    el("li", {}, p.ai_real_notes_allowed ? "Real notes may be sent to the AI (clinical mode, business associate agreement confirmed)."
      : "Real or typed notes are never sent to the AI in this deployment."),
    el("li", {}, `Saved AI requests and responses are deleted after ${p.retention_days} days; nurse decisions and history are kept.`),
    el("li", {}, "The screen locks after a few idle minutes, and spoken alerts never include a patient's name."));
  $("priv-glass").replaceChildren(...(p.break_glass_recent.length ? p.break_glass_recent.map((g) => el("tr", {},
    el("td", {}, hhmm(g.at)), el("td", {}, g.by), el("td", {}, g.patient), el("td", {}, g.reason)))
    : [el("tr", {}, el("td", { colspan: "4" }, "None."))]));
}

let lockTimer = null, lockMinutes = 3, locked = false;
function armLock() {
  clearTimeout(lockTimer);
  if (locked || !S) return;
  lockTimer = setTimeout(lockScreen, lockMinutes * 60000);
}
function lockScreen() {
  if (!S || locked) return;
  locked = true;
  $("lock-who").textContent = `Signed in as ${S.me.name}, ${S.me.title}. Enter your password to continue.`;
  $("lock-error").textContent = "";
  $("lock").hidden = false;
  $("app").inert = true;
  $("app").setAttribute("aria-hidden", "true");
  $("pins").hidden = true;
  $("lock-h").focus();
}
async function unlockScreen(password) {
  try { await api("POST", "/api/unlock", { password }); }
  catch (e) { $("lock-error").textContent = e.message === "forbidden" ? "Wrong password." : e.message; $("lock-pass").select(); return; }
  locked = false;
  $("lock").hidden = true; $("lock-pass").value = "";
  $("app").inert = false; $("app").removeAttribute("aria-hidden");
  renderPins();
  armLock();
  $("main").focus();
}
function privacyBind() {
  fetch("/api/config").then((r) => r.json()).then((c) => { lockMinutes = c.lock_minutes || 3; armLock(); }).catch(() => {});
  ["keydown", "pointerdown", "wheel", "touchstart"].forEach((ev) => document.addEventListener(ev, () => { if (!locked) armLock(); }, { passive: true }));
  $("lock-now").addEventListener("click", lockScreen);
  $("lock-form").addEventListener("submit", (e) => { e.preventDefault(); unlockScreen($("lock-pass").value); });
  $("lock-signout").addEventListener("click", async () => { try { await api("POST", "/api/logout", {}); } catch (_) { /* ignore */ } location.reload(); });
  $("hide-names").addEventListener("click", () => {
    hideNames = !hideNames;
    $("hide-names").setAttribute("aria-pressed", String(hideNames));
    $("hide-names").textContent = hideNames ? "Show names" : "Hide names";
    if (S) renderQueue();
    if (openDetail && openDetail.kind === "encounter") openEncounter(openDetail.id, true, openDetail.tab);
    say(hideNames ? "Patient names hidden on screen." : "Patient names shown.");
  });
}
document.addEventListener("DOMContentLoaded", privacyBind);

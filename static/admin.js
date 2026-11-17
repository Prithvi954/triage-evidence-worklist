"use strict";

const ROLE_TEXT = { rn: "Registered nurse", charge_rn: "Charge nurse", provider: "Provider (resident / attending)",
  admin: "Administrator", student: "Student", instructor: "Instructor" };

async function loadUsers() {
  const users = await api("GET", "/api/admin/users");
  $("users-cap").textContent = `${users.length} accounts in your unit`;
  $("users-body").replaceChildren(...users.map((u) => el("tr", {},
    el("td", {}, u.name), el("td", {}, u.username || "—"), el("td", {}, u.role_text),
    el("td", {}, u.active ? "Active" : "Deactivated"),
    el("td", {}, el("button", { type: "button", onclick: async () => {
      try { await api("POST", `/api/admin/users/${u.id}/${u.active ? "deactivate" : "activate"}`, {});
        say(`${u.name} ${u.active ? "deactivated" : "activated"}.`); loadUsers(); }
      catch (e) { say(e.message); }
    } }, u.active ? `Deactivate ${u.name}` : `Activate ${u.name}`)))));
}

document.addEventListener("DOMContentLoaded", async () => {
  const me = await pageInit(["admin"]);
  $("nu-role").replaceChildren(...me.creatable_roles.map((r) => el("option", { value: r }, ROLE_TEXT[r] || r)));
  $("new-user").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("new-result");
    try {
      await api("POST", "/api/admin/users", { name: $("nu-name").value, username: $("nu-username").value,
        role: $("nu-role").value, title: $("nu-title").value, password: $("nu-pass").value });
      out.textContent = `Account created for ${$("nu-name").value}. Give them the temporary password in person.`;
      e.target.reset(); loadUsers();
    } catch (err) { out.textContent = err.message; }
    out.focus();
  });
  $("pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("pw-result");
    try { await api("POST", "/api/me/password", { old: $("pw-old").value, new: $("pw-new").value });
      out.textContent = "Password changed."; e.target.reset(); }
    catch (err) { out.textContent = err.message; }
    out.focus();
  });
  loadUsers();
  loadFhir();
  $("fhir-sync").addEventListener("click", async () => {
    const out = $("fhir-result");
    try { const r = await api("POST", "/api/admin/fhir/sync", {});
      out.textContent = `Synced: ${r.encounters} new encounters, ${r.notes} new notes, ${r.closed} closed.`; loadFhir(); }
    catch (e) { out.textContent = e.message === "bad_request" ? "Sync failed." : e.message; }
    out.focus();
  });
  $("page-h").focus();
});

async function loadFhir() {
  const f = await api("GET", "/api/admin/fhir");
  const st = f.status[0];
  $("fhir-status").textContent = !f.configured ? "Not configured. The unit uses the simulator only."
    : !st ? "Configured; not synced yet."
    : `Last sync ${ymdhm(st.last_sync_at)}${st.last_error ? ` failed (${st.last_error})` : " OK"} · ${st.encounters} encounters and ${st.notes} notes imported so far.`;
  $("fhir-sync").disabled = !f.configured;
}

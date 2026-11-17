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
  $("page-h").focus();
});

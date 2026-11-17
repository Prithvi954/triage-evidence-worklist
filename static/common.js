// Shared helpers for the secondary pages (admin, learning hub). Text is set with textContent only.
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

async function api(method, path, body) {
  const opt = { method, headers: { "X-Requested-With": "triage-ui" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { data = null; }
  if (r.status === 401) { location.href = "/"; throw new Error("unauthenticated"); }
  if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return data;
}

function say(msg) {
  const live = $("announcer");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => { live.textContent = msg; }, 40);
}

function hhmm(iso) { return iso ? iso.slice(11, 16) : ""; }
function ymdhm(iso) { return iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : ""; }

async function pageInit(expectRoles) {
  const me = await api("GET", "/api/me");
  if (expectRoles && !expectRoles.includes(me.role)) { location.href = me.home || "/"; throw new Error("wrong page"); }
  $("s-user").textContent = `${me.name}, ${me.title}`;
  $("avatar").textContent = me.name[0];
  $("logout").addEventListener("click", async () => { await api("POST", "/api/logout", {}); location.href = "/"; });
  return me;
}

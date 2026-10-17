// Drone Studio page: collect the settings, create the job, upload the files, start it, then poll its state.
"use strict";
const $ = (id) => document.getElementById(id);
const LISTING = ["price", "beds", "baths", "area", "area_unit", "brokerage", "phone", "email", "website"];
const FIELDS = ["clips_folder", "music_path", "title", "sub", "end", "end_sub", "notes", "output_dir", "quality", "editor",
  "vertical_max_seconds", "max_value", "max_unit", "colour", "agent", ...LISTING.map((k) => "l_" + k)];
const STORE = "drone-studio-form";
// what changes with the style; a field still holding the other style's default takes this one's
const STYLE = {
  drone: { max: 45, heading: "Cards", title: ["Title", "Hollywood Beach"], sub: ["Line above the title", "Florida · Oct 2026"],
    music: "MP3, WAV, M4A · you need the rights to use it",
    notes: "Mood, pace, must-have shots, things to avoid. E.g. calm and warm, open on the open sea, slow motion on the wave, no top-down shots." },
  realestate: { max: 60, heading: "Listing", title: ["Address", "1420 N Ocean Drive"], sub: ["City / neighbourhood", "Hollywood, FL"],
    music: "Calm, 80–110 BPM · a licence that allows commercial use",
    notes: "What sells this house, the mood, must-have shots, things to avoid. E.g. bright and airy, open on the ocean view, show the renovated kitchen, skip the garage." },
};
const FOOTAGE_HINT = {
  mixed: "Drone exteriors open and close the film; the rooms come in tour order. Phone and gimbal clips are fine, portrait ones go in the short.",
  exterior: "Aerials and outside shots only: the setting, the façade, the lot, the pool, the surroundings.",
};
let clipFiles = [];      // File objects waiting to upload
let musicFile = null;
let logoFile = null;
let current = null;      // job id on screen
let timer = null;

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function mb(n) { return n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : (n / 1e6).toFixed(1) + " MB"; }
function radio(name) { return document.querySelector(`input[name=${name}]:checked`).value; }
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// ---- form state ------------------------------------------------------------------------------------------
function remember() {
  const v = {};
  FIELDS.forEach((f) => { v[f] = $(f).value; });
  v.clips_mode = radio("clips_mode"); v.music_mode = radio("music_mode"); v.style = radio("style"); v.footage = radio("footage");
  v.labels = $("labels").checked;
  v.versions = [...document.querySelectorAll("input[name=versions]:checked")].map((x) => x.value);
  try { localStorage.setItem(STORE, JSON.stringify(v)); } catch (e) { /* private mode: fine */ }
}
function restore() {
  let v = null;
  try { v = JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { v = null; }
  if (!v) return;
  FIELDS.forEach((f) => { if (v[f] !== undefined && v[f] !== null) $(f).value = v[f]; });
  if (typeof v.labels === "boolean") $("labels").checked = v.labels;
  for (const name of ["clips_mode", "music_mode", "style", "footage"]) {
    const el = document.querySelector(`input[name=${name}][value="${v[name]}"]`);
    if (el) el.checked = true;
  }
  if (Array.isArray(v.versions)) document.querySelectorAll("input[name=versions]").forEach((x) => { x.checked = v.versions.includes(x.value); });
}
function syncModes() {
  const c = radio("clips_mode"), m = radio("music_mode");
  document.querySelector("[data-mode=clips-upload]").hidden = c !== "upload";
  document.querySelector("[data-mode=clips-folder]").hidden = c !== "folder";
  document.querySelector("[data-mode=music-upload]").hidden = m !== "upload";
  document.querySelector("[data-mode=music-path]").hidden = m !== "path";
  $("vmaxField").hidden = !document.querySelector("input[name=versions][value=vertical]").checked;
  const st = radio("style"), S = STYLE[st];
  document.querySelectorAll("[data-style]").forEach((el) => { el.hidden = el.dataset.style !== st; });
  $("cardsHeading").textContent = S.heading;
  $("titleLabel").textContent = S.title[0]; $("title").placeholder = S.title[1];
  $("subLabel").textContent = S.sub[0]; $("sub").placeholder = S.sub[1];
  $("musicHint").textContent = S.music; $("notes").placeholder = S.notes;
  $("footageHint").textContent = FOOTAGE_HINT[radio("footage")];
}
function switchStyle() {          // defaults follow the style unless the user changed them
  const st = radio("style"), other = STYLE[st === "drone" ? "realestate" : "drone"];
  if ($("max_unit").value === "1" && +$("max_value").value === other.max) $("max_value").value = STYLE[st].max;
}
function settings() {
  const s = {
    clips_mode: radio("clips_mode"), clips_folder: $("clips_folder").value, music_mode: radio("music_mode"), music_path: $("music_path").value,
    max_seconds: parseFloat($("max_value").value) * parseFloat($("max_unit").value),
    vertical_max_seconds: parseFloat($("vertical_max_seconds").value),
    versions: [...document.querySelectorAll("input[name=versions]:checked")].map((x) => x.value),
    quality: $("quality").value, editor: $("editor").value,
    title: $("title").value, sub: $("sub").value, end: $("end").value, end_sub: $("end_sub").value,
    notes: $("notes").value, output_dir: $("output_dir").value,
    style: radio("style"), footage: radio("footage"), labels: $("labels").checked, colour: $("colour").value,
    listing: Object.fromEntries(LISTING.map((k) => [k, $("l_" + k).value])),
  };
  if (s.style === "realestate") s.end = $("agent").value;
  return s;
}

// ---- files -----------------------------------------------------------------------------------------------
function renderFiles() {
  $("clipList").innerHTML = clipFiles.map((f, i) => `<li><span>${esc(f.name)}</span><span class="size">${mb(f.size)}</span>
    <button type="button" aria-label="Remove ${esc(f.name)}" data-rm="${i}">✕</button><span class="bar"><i id="up-clip-${i}"></i></span></li>`).join("");
  $("musicList").innerHTML = musicFile ? `<li><span>${esc(musicFile.name)}</span><span class="size">${mb(musicFile.size)}</span>
    <button type="button" aria-label="Remove track" data-rm-music>✕</button><span class="bar"><i id="up-music"></i></span></li>` : "";
  $("logoList").innerHTML = logoFile ? `<li><span>${esc(logoFile.name)}</span><span class="size">${mb(logoFile.size)}</span>
    <button type="button" aria-label="Remove logo" data-rm-logo>✕</button><span class="bar"><i id="up-logo"></i></span></li>` : "";
}
const VIDEO = /\.(mp4|mov|m4v|mxf)$/i, AUDIO = /\.(mp3|wav|m4a|aac|flac|ogg|aiff?)$/i;
function addClips(list) {
  for (const f of list) if (VIDEO.test(f.name) && !clipFiles.some((x) => x.name === f.name && x.size === f.size)) clipFiles.push(f);
  renderFiles();
}
function setMusic(list) { const f = [...list].find((x) => AUDIO.test(x.name)); if (f) { musicFile = f; renderFiles(); } }
const IMAGE = /\.(png|jpe?g|webp)$/i;
function setLogo(list) { const f = [...list].find((x) => IMAGE.test(x.name)); if (f) { logoFile = f; renderFiles(); } }
function wireDrop(zone, input, onFiles) {
  input.addEventListener("change", () => { onFiles(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (ev) => { ev.preventDefault(); onFiles(ev.dataTransfer.files); });
}
function upload(jobId, kind, file, barId) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", `/api/jobs/${jobId}/upload/${kind}/${encodeURIComponent(file.name)}`);
    x.upload.onprogress = (e) => { const b = $(barId); if (b && e.lengthComputable) b.style.width = (100 * e.loaded / e.total) + "%"; };
    x.onload = () => (x.status < 300 ? resolve() : reject(new Error(JSON.parse(x.responseText || "{}").error || "upload failed")));
    x.onerror = () => reject(new Error("upload failed: " + file.name));
    x.send(file);
  });
}

// ---- create ----------------------------------------------------------------------------------------------
async function create(ev) {
  ev.preventDefault();
  const err = $("formError"); err.hidden = true;
  const s = settings();
  const re = s.style === "realestate";
  const problem = !s.title.trim() ? (re ? "Enter the property's address." : "Give the film a title.") :
    s.clips_mode === "upload" && !clipFiles.length ? "Add some clips." :
    s.music_mode === "upload" && !musicFile ? "Add a music track." :
    !(s.max_seconds >= 10) ? "The maximum length must be at least 10 seconds." :
    !s.versions.length ? "Pick at least one version." : "";
  if (problem) { err.textContent = problem; err.hidden = false; return; }
  remember();
  const btn = $("create"); btn.disabled = true; btn.textContent = "Creating…";
  try {
    const { id } = await api("POST", "/api/jobs", s);
    showJob(id, s.title, "Uploading…");
    if (s.clips_mode === "upload") {
      for (let i = 0; i < clipFiles.length; i++) {
        btn.textContent = `Uploading clip ${i + 1} of ${clipFiles.length}…`;
        await upload(id, "raw", clipFiles[i], `up-clip-${i}`);
      }
    }
    if (s.music_mode === "upload") { btn.textContent = "Uploading music…"; await upload(id, "music", musicFile, "up-music"); }
    if (re && logoFile) { btn.textContent = "Uploading logo…"; await upload(id, "brand", logoFile, "up-logo"); }
    await api("POST", `/api/jobs/${id}/start`);
    poll();
    loadHistory();
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
  } finally {
    btn.disabled = false; btn.textContent = "Create video";
  }
}

// ---- job view --------------------------------------------------------------------------------------------
function showJob(id, title, state) {
  current = id;
  try { history.replaceState(null, "", "#job=" + encodeURIComponent(id)); } catch (e) { /* file:// or sandboxed: fine */ }
  $("empty").hidden = true; $("job").hidden = false;
  $("jobTitle").textContent = title || "";
  $("jobState").textContent = state || "";
  $("stages").innerHTML = ""; $("outputs").innerHTML = ""; $("summary").hidden = true; $("cutBox").hidden = true;
  $("revise").hidden = true; $("jobError").hidden = true; $("activity").hidden = true;
}
const STATE_TEXT = { draft: "Waiting for files", queued: "Queued", running: "Working", done: "Done", failed: "Stopped with an error", cancelled: "Cancelled" };
function renderJob(j) {
  $("jobTitle").textContent = j.settings.title;
  const used = j.editor_used ? ` · edited by ${j.editor_used === "claude" ? "Claude" : "the automatic planner"}` : "";
  $("jobState").textContent = (STATE_TEXT[j.state] || j.state) + used;
  $("stages").innerHTML = j.stages.map((s) => `<li class="${s.state}"><span class="dot" aria-hidden="true"></span>
    <span class="label">${esc(s.label)}</span><span class="detail">${esc(s.detail || "")}</span>
    ${s.state === "running" && s.progress ? `<span class="bar"><i style="width:${(100 * s.progress).toFixed(0)}%"></i></span>` : ""}</li>`).join("");
  $("activity").hidden = !(j.activity && j.state === "running");
  $("activity").textContent = j.activity || "";
  $("jobError").hidden = !j.error; $("jobError").textContent = j.error || "";
  $("cancel").hidden = !["queued", "running"].includes(j.state);
  $("summary").hidden = !(j.summary && j.state === "done"); $("summary").textContent = j.summary || "";
  const outKey = JSON.stringify(j.outputs.map((o) => [o.file, o.delivered, o.verify_pass]));
  if ($("outputs").dataset.key !== outKey) {
    $("outputs").dataset.key = outKey;
    $("outputs").innerHTML = j.outputs.map((o) => `<div class="out">
      <video controls preload="metadata" src="/media/${j.id}/${o.file}?v=${o.size}"></video>
      <div class="meta"><strong>${esc(o.label)}</strong>
        <span class="badge ${o.verify_pass ? "ok" : "bad"}" title="${esc(o.verify.map((r) => r.join(": ")).join("\n"))}">${o.verify_pass ? "verified" : "check failed"}</span>
        <span>${mb(o.size)}</span>
        ${o.delivered ? `<button type="button" class="ghost" data-reveal="${esc(o.delivered)}">Show in Finder</button>` : ""}
        ${o.delivered ? `<span class="path-text">${esc(o.delivered)}</span>` : ""}</div></div>`).join("");
  }
  if (j.cut && j.cut.shots) {
    $("cutBox").hidden = false;
    $("cutRows").innerHTML = j.cut.shots.map((s) => `<tr><td>${s.at.toFixed(1)} s</td><td>${esc(s.clip)}</td><td>${s.in.toFixed(1)} s</td>
      <td>${s.dur.toFixed(1)} s</td><td>${s.speed === 1 ? "" : s.speed + "×"}</td><td>${esc(s.transition.type)}</td><td>${esc(s.note || "")}</td></tr>`).join("");
  }
  $("revise").hidden = !["done", "failed"].includes(j.state) || !j.stages.some((s) => s.key === "edit");
  const log = $("log"); const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = j.log || ""; if (atEnd) log.scrollTop = log.scrollHeight;
  return ["queued", "running", "draft"].includes(j.state);
}
async function poll() {
  clearTimeout(timer);
  if (!current) return;
  const id = current;
  try {
    const j = await api("GET", `/api/jobs/${id}`);
    if (id !== current) return;
    const live = renderJob(j);
    if (live) timer = setTimeout(poll, 1000); else loadHistory();
  } catch (e) { timer = setTimeout(poll, 3000); }
}
async function loadHistory() {
  const list = await api("GET", "/api/jobs").catch(() => []);
  $("history").innerHTML = list.map((j) => `<li><button type="button" data-job="${esc(j.id)}" data-title="${esc(j.title)}">
    <span>${esc(j.title)}</span><span class="when">${esc(STATE_TEXT[j.state] || j.state)} · ${esc(j.created.replace("T", " ").slice(0, 16))}</span></button></li>`).join("")
    || `<li class="when">No films yet.</li>`;
}
async function checks() {
  const c = await api("GET", "/api/check").catch(() => null);
  if (!c) return;
  const chip = (cls, text) => `<span class="chip ${cls}">${esc(text)}</span>`;
  $("checks").innerHTML = [
    chip(c.ffmpeg ? "ok" : "bad", "ffmpeg"), chip(c.numpy ? "ok" : "bad", "numpy"), chip(c.chrome ? "ok" : "bad", "Chrome"),
    chip(c.claude ? "ok" : "warn", c.claude ? "Claude" : "Claude CLI missing: automatic edits only"),
    chip(c.lut ? "ok" : "warn", c.lut ? "LUT" : "no LUT: approximate colour"),
  ].join("");
  if (!$("output_dir").value) $("output_dir").placeholder = c.default_output;
  if (!c.picker) document.querySelectorAll("[data-pick]").forEach((b) => { b.hidden = true; });
  if (!c.claude) $("editor").value = "auto";
}

// ---- wiring ----------------------------------------------------------------------------------------------
wireDrop($("clipDrop"), $("clipFiles"), addClips);
wireDrop($("musicDrop"), $("musicFile"), setMusic);
wireDrop($("logoDrop"), $("logoFile"), setLogo);
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("button");
  if (!t) return;
  if (t.dataset.rm !== undefined) { clipFiles.splice(+t.dataset.rm, 1); renderFiles(); }
  else if (t.dataset.rmMusic !== undefined) { musicFile = null; renderFiles(); }
  else if (t.dataset.rmLogo !== undefined) { logoFile = null; renderFiles(); }
  else if (t.dataset.pick) {
    t.disabled = true;
    try { const r = await api("POST", "/api/pick", { kind: t.dataset.pick }); if (r.path) { $(t.dataset.target).value = r.path; remember(); } }
    finally { t.disabled = false; }
  } else if (t.dataset.job) { showJob(t.dataset.job, t.dataset.title, "Loading…"); poll(); }
  else if (t.dataset.reveal) { api("POST", "/api/reveal", { id: current, path: t.dataset.reveal }).catch(() => {}); }
});
$("cancel").addEventListener("click", () => current && api("POST", `/api/jobs/${current}/cancel`).then(poll));
$("revise").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const notes = $("reviseNotes").value.trim();
  if (!notes) return;
  try { await api("POST", `/api/jobs/${current}/revise`, { notes }); $("reviseNotes").value = ""; $("outputs").dataset.key = ""; poll(); }
  catch (e) { $("jobError").hidden = false; $("jobError").textContent = e.message; }
});
$("form").addEventListener("submit", create);
$("form").addEventListener("change", (ev) => {
  if (ev.target.name === "style") switchStyle();
  if (ev.target.name === "footage") $("labels").checked = radio("footage") === "mixed";
  syncModes(); remember();
});
restore(); syncModes(); renderFiles(); checks(); loadHistory();
const fromHash = decodeURIComponent((location.hash.match(/^#job=(.+)$/) || [])[1] || "");
if (fromHash) { showJob(fromHash, "", "Loading…"); poll(); }

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
let state = null,
  busy = false,
  automatic = false,
  tasksLoaded = false;
const task = (name) => state?.tasks?.find((t) => t.name === name);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value) => `${(value * 100).toFixed(value < 0.01 ? 1 : 0)}%`;
async function call(name, body = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed");
  state = data;
  render();
  return data;
}
function controls() {
  const live = state?.page && !["done", "blocked"].includes(state.status);
  $("start").disabled = busy;
  $("scenario").disabled = busy;
  $("goal").disabled = busy;
  $("start-url").disabled = busy;
  $("reasoning").disabled = busy || !state?.system2?.available;
  $("suite").disabled = busy;
  $("download-results").disabled = !state?.results?.length;
  $("choose").disabled = busy || !live;
  $("execute").disabled = busy || !state?.decision || !live;
  $("auto").disabled = busy || !live;
  $("auto").hidden = automatic;
  $("stop").hidden = !automatic;
  $("download").disabled = !state?.history?.length;
}
async function perform(fn, label) {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  controls();
  $("status").textContent = label;
  try {
    await fn();
  } catch (error) {
    automatic = false;
    try {
      state = await fetch("/api/state").then((r) => r.json());
      render();
    } catch {
      /* Preserve the original failure if the server disconnected. */
    }
    $("error").textContent = error.message;
    $("error").hidden = false;
    $("status").textContent = "Paused · needs attention";
  } finally {
    busy = false;
    controls();
  }
}
function loadTasks() {
  if (tasksLoaded || !state?.tasks) return;
  tasksLoaded = true;
  const suites = [...new Set(state.tasks.map((t) => t.suite))];
  $("scenario").insertAdjacentHTML(
    "afterbegin",
    suites
      .map((suite) => `<optgroup label="${escape(suite)} · verified">${state.tasks
        .filter((t) => t.suite === suite)
        .map((t) => `<option value="${escape(t.name)}">${escape(t.label)}</option>`)
        .join("")}</optgroup>`)
      .join(""),
  );
  $("scenario").value = state.run?.task || state.tasks[0].name;
  selectTask();
  const s2 = state.system2;
  $("system2-model").textContent = s2.available ? s2.model : "not configured";
  $("reasoning").closest("label").title = s2.available ? "Jev decides when a reasoning model takes over a step" : s2.error;
  $("reasoning").checked = s2.available;
}
function selectTask() {
  const t = task($("scenario").value);
  $("start-url").readOnly = Boolean(t);
  $("start-url").value = t ? t.url : "";
  $("goal").value = t ? t.goal : "";
}
function renderVerdict() {
  const v = state.run?.verification;
  $("verdict").hidden = !v;
  if (!v) return;
  const title = v.passed === true ? "✓ Verified" : v.passed === false ? "✗ Not achieved" : "— Not verified";
  $("verdict").className = `verdict ${v.passed === true ? "pass" : v.passed === false ? "fail" : ""}`;
  const checks = Object.entries(v.checks || {});
  $("verdict").innerHTML =
    `<strong>${title}</strong>${escape(state.run.label)} · ${escape(state.status === "done" ? "Jev chose DONE" : "run stopped")}` +
    (v.note ? ` · ${escape(v.note)}` : "") +
    (checks.length ? `<ul>${checks.map(([k, ok]) => `<li>${ok ? "✓" : "✗"} ${escape(k)}</li>`).join("")}</ul>` : "");
}
function renderSystem2(d) {
  const jev = state.decisions?.at(-1);
  const on = state.system2?.available && state.decisions?.length && jev?.system2 != null;
  $("system2").hidden = !on;
  if (!on) return;
  const threshold = 0.5, ask = jev.system2;
  const review = d?.system === 2 ? d : null;
  const late = state.reviews?.at(-1)?.fingerprint === jev.fingerprint ? state.reviews.at(-1) : null;
  let body = `<p>Jev handled this step.</p>`;
  if (review)
    body = `<p><span class="badge s2">System 2 · ${escape(review.model)} · ${review.latency_ms} ms</span>${review.agrees ? "Agreed with Jev" : `Overrode Jev's ${escape(review.proposal?.operation)}`}</p>` +
      (review.analysis ? `<p>${escape(review.analysis)}</p>` : "") +
      (review.note ? `<p><b>Note to Jev:</b> ${escape(review.note)}</p>` : "");
  else if (late?.failed) body = `<p><span class="badge s2">System 2</span>${escape(late.failed)} · Jev's choice stands.</p>`;
  $("system2").innerHTML =
    `<div class="ask"><span>Jev asks for System 2</span><b>${percent(ask)}</b></div>` +
    `<div class="meter"><i class="${ask >= threshold ? "hot" : ""}" style="width:${ask * 100}%"></i><u style="left:${threshold * 100}%"></u></div>` +
    body;
}
const money = (v) => (v ? `$${v < 0.01 ? v.toFixed(5) : v.toFixed(3)}` : "—");
function runCost() {
  const sum = (rows) => (rows || []).reduce((total, r) => total + (r.usage?.cost || 0), 0);
  return sum(state.decisions) + sum(state.reviews) + sum(state.text_calls);
}
function renderGates(d, page) {
  // Every gate code applies to this step, with the value Jev or the run produced beside its threshold.
  const jev = state.decisions?.at(-1);
  if (!jev || !page) return ($("gates").innerHTML = '<tr><td class="off">Choose next to see the gates.</td></tr>');
  const chosen = page.actions.find((a) => a.id === d?.choice);
  const repeats = chosen ? state.history.filter((h) => h.kind === chosen.kind && h.action === chosen.label).length : 0;
  const review = d?.system === 2 ? d : state.reviews?.at(-1)?.fingerprint === jev.fingerprint ? state.reviews.at(-1) : null;
  const target = jev.target != null ? jev.target_probabilities[jev.target] : null;
  const rows = [
    ["Jev operation", percent(jev.operation_probabilities[jev.operation]), "valid choice", "ok"],
    ["Jev target", target != null ? percent(target) : "—", target != null ? "observed index" : "not needed", target != null ? "ok" : "off"],
    jev.system2 == null
      ? ["Asks System 2", "—", "off", "off"]
      : ["Asks System 2", percent(jev.system2), "≥ 50%", jev.system2 >= 0.5 ? "hit" : "ok"],
    ["System 2 deadline", review ? `${(review.latency_ms / 1000).toFixed(1)} s` : "—", "≤ 10 s",
      review?.failed ? "hit" : review ? "ok" : "off"],
    ["Same action + argument", chosen ? `${repeats} ran` : "—", "4th refused", repeats >= 3 ? "hit" : chosen ? "ok" : "off"],
  ];
  $("gates").innerHTML = rows
    .map(([name, value, limit, cls]) => `<tr><td>${name}</td><td class="${cls}">${value}</td><td class="off">${limit}</td></tr>`)
    .join("");
}
function renderResults() {
  const rows = state.results || [];
  $("result-count").textContent = `${rows.length} runs · ${rows.filter((r) => r.passed).length} verified`;
  $("results").innerHTML = rows.length
    ? `<table class="results"><thead><tr><th>Task</th><th>Mode</th><th>Result</th><th class="num">Time</th><th class="num">Actions</th><th class="num">Jev calls</th><th class="num">System 2</th></tr></thead><tbody>${rows
        .map(
          (r) =>
            `<tr><td>${escape(r.label)}</td><td>${escape(r.mode)}</td><td class="${r.passed ? "pass-text" : r.passed === false ? "fail-text" : ""}">${r.passed ? "Verified" : r.passed === false ? "Failed" : "Unverified"} · ${escape(r.status)}</td><td class="num">${(r.ms / 1000).toFixed(2)} s</td><td class="num">${r.actions}</td><td class="num">${r.system1_calls}</td><td class="num">${r.system2_calls} · ${r.system2_overrides} overrides${r.system2_late ? ` · ${r.system2_late} late` : ""}</td></tr>`,
        )
        .reverse()
        .join("")}</tbody></table>`
    : '<p class="muted">Finished runs appear here with their independent verification.</p>';
}
function render() {
  if (!state) return;
  loadTasks();
  renderVerdict();
  renderResults();
  $("system2-tag").textContent = state.system2?.available ? "+ System 2 + text helper" : "+ text helper";
  $("helper").textContent = `Text helper · ${state.text_model}`;
  $("plan").innerHTML = (state.plan || [])
    .map(
      (goal, i) =>
        `<div class="plan-step ${i === state.plan_index ? "current" : ""}"><span>${i < state.plan_index ? "✓" : i + 1}</span>${escape(goal)}</div>`,
    )
    .join("");
  const page = state.page,
    d =
      state.decision ||
      (state.status === "done" ? state.decisions?.at(-1) : null);
  const labels = {
    idle: "Ready to explore",
    ready: "Page observed · ready for a decision",
    predicted: "Choice ready · inspect or execute",
    done: "Jev chose DONE · see the verification",
    blocked: "Stopped · no supported next action",
  };
  $("status").textContent = labels[state.status] || state.status;
  if (!page) {
    controls();
    return;
  }
  $("empty").hidden = true;
  $("screenshot").hidden = false;
  $("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  $("url").textContent = page.url;
  $("page-title").textContent = page.title;
  $("action-count").textContent = `${state.elements.length} elements`;
  const chosen = page.actions.find((a) => a.id === d?.choice);
  $("choice-title").textContent = d
    ? chosen?.label || d.choice
    : "Choose an action";
  $("latency").textContent = d ? `${d.latency_ms} ms` : "—";
  $("confidence").textContent = d?.target_confidence != null ? percent(d.target_confidence) : "—";
  $("completion").textContent = d ? d.operation : "—";
  $("ranking-note").textContent = d?.system === 2 ? "Ranked by Jev · chosen by System 2" : d ? "Ranked by Jev" : "Unranked";
  renderSystem2(d);
  renderGates(d, page);
  $("cost").textContent = money(runCost());
  // A System 2 decision has no distribution of its own; show Jev's beside System 2's pick.
  const dist = d?.system === 2 ? state.decisions.at(-1) : d;
  const op = Object.entries(dist?.operation_probabilities || {}).sort((a,b)=>b[1]-a[1]);
  $("operation-choices").innerHTML = op.map(([name,p]) =>
    `<span class="operation-choice ${name === d.operation ? 'best' : ''}">${escape(name)} <b>${percent(p)}</b></span>`).join('');
  const probability = e => dist?.target_probabilities[e.index] ??
    Math.max(-1, ...(e.options || []).map(o=>dist?.target_probabilities[o.index] ?? -1));
  const selectedIndex = d?.target?.split(':')[0];
  const elements = [...state.elements];
  if (d) elements.sort((a,b)=>probability(b)-probability(a));
  $("choices").innerHTML = elements.map(e => {
    const p = probability(e);
    return `<div class="choice ${selectedIndex === e.index ? 'best' : ''}" data-action="${escape(e.index)}"><span class="choice-id">[${escape(e.index)}]</span><div class="choice-label">${escape(e.label)}<small>${escape(e.role)} · ${escape(e.operations.join(' / '))}${e.value ? ' · '+escape(e.value) : ''}${e.checked !== undefined ? ' · checked '+escape(e.checked) : ''}</small>${p >= 0 ? `<div class="bar" style="--probability:${p*100}%"></div>` : ''}</div><span class="probability">${p >= 0 ? percent(p) : '—'}</span></div>`;
  }).join('');
  const targets = new Map();
  // Same numbering as Jev's element table: covered controls are not offered, so they get no number.
  for (const a of page.actions) if (a.rect && !a.covered && !targets.has(a.node)) targets.set(a.node, a);
  $("targets").innerHTML = [...targets.values()].map((a,i) => {
    const index=String(i+1);
    return `<div class="target ${index === selectedIndex ? 'selected' : ''}" data-action="${index}" style="left:${100*a.rect.x/page.w}%;top:${100*a.rect.y/page.h}%;width:${100*a.rect.w/page.w}%;height:${100*a.rect.h/page.h}%"><span>${index}</span></div>`;
  }).join('');
  $("targets").hidden = !$("overlays").checked;
  $("history").innerHTML = state.history.length
    ? state.history
        .map(
          (h) =>
            `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div><span class="badge ${h.system === 2 ? "s2" : ""}">S${h.system || 1}</span>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}${h.note ? `<span class="note">${escape(h.note)}</span>` : ""}</div><span class="time">${h.latency_ms} ms · ${h.system === 2 ? "System 2" : percent(h.probability)}</span><span class="effect">${h.page_changed ? "Page changed" : "No change observed"}</span></div>`,
        )
        .join("")
    : '<p class="muted">Each executed action leaves an observed result.</p>';
  $("step-count").textContent = `${state.history.length} actions · ${(state.elapsed_ms / 1000).toFixed(2)} s`;
  $("model-state").textContent = JSON.stringify(
    d?.request || {
      goal: state.goal,
      url: page.url,
      text: page.text,
      actions: page.actions.map(({ rect, node, ...rest }) => rest),
    },
    null,
    2,
  );
  controls();
}
$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  automatic = false;
  perform(async () => {
    await start($("scenario").value, $("goal").value, $("reasoning").checked);
    // A run goes straight to its end; Pause stops after the current request, and step controls resume from there.
    automatic = true;
    controls();
    $("status").textContent = "Running…";
    await runToEnd();
    automatic = false;
  }, "Opening a fresh browser…");
});
$("scenario").addEventListener("change", selectTask);
const start = (name, goal, reasoning) =>
  call("reset", { task: name, goal, url: $("start-url").value, reasoning });
async function runToEnd() {
  for (let i = 0; i < state.max_steps * 2 && automatic; i++) {
    if ($("pace").checked) {
      await call("predict");
      await new Promise(resolve => setTimeout(resolve, 450));
      if (!automatic) break;
      await call("act", {fingerprint: state.page.fingerprint});
    } else {
      await call("tick");
    }
    if (["done", "blocked"].includes(state.status)) break;
  }
}
$("suite").addEventListener("click", () =>
  perform(async () => {
    automatic = true;
    controls();
    const modes = [$("reasoning").checked && state.system2?.available, ...($("both-modes").checked && $("reasoning").checked ? [false] : [])];
    const suite = task($("scenario").value)?.suite || "core";
    for (const t of state.tasks.filter((x) => x.suite === suite)) {
      for (const reasoning of modes) {
        if (!automatic) return;
        $("status").textContent = `Suite · ${t.label} · ${reasoning ? "System 1 + 2" : "System 1"}`;
        try {
          $("start-url").value = t.url;
          await start(t.name, t.goal, reasoning);
          await runToEnd();
        } catch (error) {
          // One failed run is a result, not a reason to stop the suite.
          await call("stop", { reason: error.message }).catch(() => {});
        }
        if (!["done", "blocked"].includes(state.status)) await call("stop", { reason: "Paused" }).catch(() => {});
      }
    }
    automatic = false;
    $("scenario").value = state.run?.task;
  }, "Running the suite…"),
);
$("download-results").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state.results, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "jev-test-results.json";
  a.click();
  URL.revokeObjectURL(url);
});
$("choose").addEventListener("click", () =>
  perform(() => call("predict"), "Jev is comparing the actions…"),
);
$("execute").addEventListener("click", () =>
  perform(
    () => call("act", { fingerprint: state.page.fingerprint }),
    "Executing the choice…",
  ),
);
$("auto").addEventListener("click", () =>
  perform(async () => {
    automatic = true;
    controls();
    $("status").textContent = "Running…";
    await runToEnd();
    automatic = false;
  }, "Running the browser…"),
);
$("stop").addEventListener("click", () => {
  automatic = false;
  $("status").textContent = "Pausing after the current request…";
  controls();
});
$("overlays").addEventListener("change", () => {
  $("targets").hidden = !$("overlays").checked;
});
$("choices").addEventListener("pointerover", (event) => {
  const id = event.target.closest("[data-action]")?.dataset.action;
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === id || t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    );
});
$("choices").addEventListener("pointerleave", () =>
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    ),
);
$("download").addEventListener("click", () => {
  const { page, ...rest } = state;
  const blob = new Blob(
    [
      JSON.stringify(
        { ...rest, page: { ...page, screenshot: undefined } },
        null,
        2,
      ),
    ],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "typesafe-browser-trace.json";
  a.click();
  URL.revokeObjectURL(url);
});
fetch("/api/state")
  .then((r) => r.json())
  .then((s) => {
    state = s;
    render();
  })
  .catch(() => {
    $("status").textContent = "Cannot reach local demo server";
  });

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let listening = null;
if (!Recognition) {
  $("mic").disabled = true;
  $("mic").title = "Speech input needs Chrome or Edge";
}
$("mic").addEventListener("click", () => {
  if (listening) return listening.stop();
  // Dictation only fills the goal; nothing runs until Start run.
  listening = new Recognition();
  listening.interimResults = true;
  listening.lang = navigator.language || "en-US";
  listening.onresult = (event) => {
    // An edited preset goal runs unverified; the server applies a preset check only to its own goal.
    $("goal").value = [...event.results].map((r) => r[0].transcript).join(" ");
  };
  listening.onend = () => {
    listening = null;
    $("mic").classList.remove("listening");
    $("mic").textContent = "Speak goal";
  };
  listening.onerror = (event) => {
    $("error").textContent = `Speech input: ${event.error}`;
    $("error").hidden = false;
  };
  listening.start();
  $("mic").classList.add("listening");
  $("mic").textContent = "Stop";
});

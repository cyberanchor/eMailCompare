// Search, filters and sorting for the provider table. The table is complete without this script.
// Filter state is kept in the URL hash, so a filtered view can be linked (e.g. #state=checked&eyes=none).
(() => {
  const root = document.querySelector(".emc-explorer");
  if (!root) return;
  const $ = (sel) => root.querySelector(sel);
  const tbody = $(".emc-table tbody");
  const rows = [...tbody.querySelectorAll("tr[data-emc-row]")];
  const empty = $(".emc-empty");
  const count = $("[data-emc-count]");
  const q = $("[data-emc-q]");
  const eyes = $("[data-emc-eyes]");
  const country = $("[data-emc-country]");
  const free = $("[data-emc-free]");
  const hideDead = $("[data-emc-hide-dead]");
  const stateButtons = [...root.querySelectorAll("[data-emc-state]")];
  const sortButtons = [...root.querySelectorAll("[data-emc-sort]")];
  let state = "";
  let sort = { key: "n", dir: "asc" };

  const readHash = () => {
    const p = new URLSearchParams(location.hash.slice(1));
    if (!["q", "state", "eyes", "country", "free", "dead", "sort"].some((k) => p.has(k))) return;
    q.value = p.get("q") || "";
    state = p.get("state") || "";
    eyes.value = p.get("eyes") || "";
    country.value = p.get("country") || "";
    free.checked = p.get("free") === "1";
    hideDead.checked = p.get("dead") !== "1";
    if (p.get("sort")) sort = { key: p.get("sort"), dir: p.get("dir") === "desc" ? "desc" : "asc" };
  };

  const writeHash = () => {
    const p = new URLSearchParams();
    if (q.value.trim()) p.set("q", q.value.trim());
    if (state) p.set("state", state);
    if (eyes.value) p.set("eyes", eyes.value);
    if (country.value) p.set("country", country.value);
    if (free.checked) p.set("free", "1");
    if (!hideDead.checked) p.set("dead", "1");
    if (sort.key !== "n" || sort.dir !== "asc") { p.set("sort", sort.key); p.set("dir", sort.dir); }
    const hash = p.toString();
    history.replaceState(null, "", hash ? "#" + hash : location.pathname + location.search);
  };

  const apply = () => {
    const terms = q.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const tr of rows) {
      const d = tr.dataset;
      const ok =
        (!state || d.state === state) &&
        (state === "dead" || !hideDead.checked || d.state !== "dead") &&
        (!eyes.value || d.eyes === eyes.value) &&
        (!country.value || d.country === country.value) &&
        (!free.checked || d.free === "1") &&
        terms.every((t) => d.s.includes(t));
      tr.hidden = !ok;
      if (ok) shown++;
    }
    empty.hidden = shown > 0;
    count.textContent = shown;
    stateButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.emcState === state)));
    sortButtons.forEach((b) => (b.dataset.emcSort === sort.key ? b.setAttribute("data-dir", sort.dir) : b.removeAttribute("data-dir")));
  };

  const order = () => {
    const key = sort.key;
    const sign = sort.dir === "desc" ? -1 : 1;
    const val = (tr) => (key === "n" ? Number(tr.dataset.n) : tr.dataset[key] || "");
    rows.sort((a, b) => {
      const x = val(a), y = val(b);
      if (key !== "n" && !x !== !y) return x ? -1 : 1; // empty values last in both directions
      return (x < y ? -1 : x > y ? 1 : 0) * sign;
    });
    rows.forEach((tr) => tbody.insertBefore(tr, empty));
  };

  const update = () => { apply(); writeHash(); };

  stateButtons.forEach((b) => b.addEventListener("click", () => { state = b.dataset.emcState; update(); }));
  sortButtons.forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.emcSort;
    sort = { key, dir: sort.key === key && sort.dir === "asc" ? "desc" : "asc" };
    order(); update();
  }));
  [eyes, country, free, hideDead].forEach((el) => el.addEventListener("change", update));
  let timer;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(update, 120); });

  // Stat cards above the table act as status shortcuts.
  document.querySelectorAll("[data-emc-set-state]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    state = a.dataset.emcSetState;
    if (state === "dead") hideDead.checked = false;
    update();
    root.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  // "/" focuses the search box, Escape clears it.
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); q.focus(); }
  });
  q.addEventListener("keydown", (e) => { if (e.key === "Escape" && q.value) { q.value = ""; update(); } });

  readHash();
  order();
  apply();
})();

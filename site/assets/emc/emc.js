// Search, filters, sorting and export for the provider table. The table is complete without this script.
// Filter state is kept in the URL hash, so a filtered view can be linked: #state=checked&sort=alliance&dir=asc
(() => {
  const root = document.querySelector(".emc-explorer");
  if (!root) return;
  const $ = (sel) => root.querySelector(sel);
  const list = $("[data-emc-list]");
  const items = [...list.querySelectorAll("[data-emc-row]")];
  const empty = $(".emc-empty");
  const count = $("[data-emc-count]");
  const q = $("[data-emc-q]");
  const country = $("[data-emc-country]");
  const stateButtons = [...root.querySelectorAll("[data-emc-state]")];
  const sortButtons = [...root.querySelectorAll("[data-emc-sort]")];
  const KEYS = ["q", "state", "country", "sort"];
  let state = "";
  let sort = { key: "n", dir: "asc" };

  const readHash = () => {
    const p = new URLSearchParams(location.hash.slice(1));
    if (!KEYS.some((k) => p.has(k))) return;
    q.value = p.get("q") || "";
    state = p.get("state") || "";
    country.value = p.get("country") || "";
    if (p.get("sort")) sort = { key: p.get("sort"), dir: p.get("dir") === "desc" ? "desc" : "asc" };
  };

  const writeHash = () => {
    const p = new URLSearchParams();
    if (q.value.trim()) p.set("q", q.value.trim());
    if (state) p.set("state", state);
    if (country.value) p.set("country", country.value);
    if (sort.key !== "n" || sort.dir !== "asc") { p.set("sort", sort.key); p.set("dir", sort.dir); }
    const hash = p.toString();
    history.replaceState(null, "", hash ? "#" + hash : location.pathname + location.search);
  };

  const apply = () => {
    const terms = q.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const el of items) {
      const d = el.dataset;
      const ok = (!state || d.state === state) && (!country.value || d.country === country.value) &&
        terms.every((t) => d.s.includes(t));
      el.hidden = !ok;
      if (ok) shown++;
    }
    empty.hidden = shown > 0;
    count.textContent = shown;
    stateButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.emcState === state)));
    sortButtons.forEach((b) => (b.dataset.emcSort === sort.key ? b.setAttribute("data-dir", sort.dir) : b.removeAttribute("data-dir")));
  };

  const order = () => {
    const { key, dir } = sort;
    const sign = dir === "desc" ? -1 : 1;
    const val = (el) => (key === "n" ? Number(el.dataset.n) : el.dataset[key] || "");
    items.sort((a, b) => {
      const x = val(a), y = val(b);
      if (key !== "n" && !x !== !y) return x ? -1 : 1; // empty values last in both directions
      return (x < y ? -1 : x > y ? 1 : 0) * sign || Number(a.dataset.n) - Number(b.dataset.n);
    });
    items.forEach((el) => list.insertBefore(el, empty));
  };

  const update = () => { apply(); writeHash(); };

  stateButtons.forEach((b) => b.addEventListener("click", () => { state = b.dataset.emcState; update(); }));
  sortButtons.forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.emcSort;
    sort = { key, dir: sort.key === key && sort.dir === "asc" ? "desc" : "asc" };
    order();
    update();
  }));
  country.addEventListener("change", update);
  let timer;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(update, 120); });

  // Stat cards act as status shortcuts.
  document.querySelectorAll("[data-emc-set-state]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    state = a.dataset.emcSetState;
    update();
    root.scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  // Export: the domains (and their other domains) of the rows shown, one per line.
  const exportButton = $("[data-emc-export]");
  if (exportButton) exportButton.addEventListener("click", () => {
    const names = [];
    items.filter((el) => !el.hidden).forEach((el) => {
      names.push(el.dataset.domain);
      (el.dataset.seconds || "").split(" ").filter(Boolean).forEach((s) => names.push(s));
    });
    const blob = new Blob([[...new Set(names)].join("\n") + "\n"], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "email-domains.txt";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  });

  // "/" focuses the search box, Escape clears it.
  document.addEventListener("keydown", (e) => {
    const el = document.activeElement;
    const typing = el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
    if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); q.focus(); }
  });
  q.addEventListener("keydown", (e) => { if (e.key === "Escape" && q.value) { q.value = ""; update(); } });

  readHash();
  order();
  apply();
})();

"use strict";

const Navigation = {
  sections: ["live", "news", "alerts", "portfolio", "risk", "dojo", "diagnostic"],
  initialized: new Set(),
  init() {
    document.querySelectorAll("[data-section]").forEach(button => {
      button.addEventListener("click", () => showSection(button.dataset.section));
    });
    document.addEventListener("click", event => {
      const more = document.querySelector(".mobile-more");
      if (more && !more.contains(event.target)) more.open = false;
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        const more = document.querySelector(".mobile-more");
        if (more) more.open = false;
      }
    });
    let section = "live";
    try { section = localStorage.getItem("lastSection") || "live"; } catch (_) {}
    showSection(section);
  },
};

function showSection(name) {
  if (!Navigation.sections.includes(name)) name = "live";
  Navigation.sections.forEach(section => {
    document.getElementById("section-" + section)?.classList.toggle("hidden", section !== name);
  });
  document.querySelectorAll("[data-section]").forEach(button => {
    const active = button.dataset.section === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  const more = document.querySelector(".mobile-more");
  if (more) {
    more.open = false;
    more.classList.toggle("active", name === "dojo" || name === "diagnostic");
  }
  if (name === "dojo" && !Navigation.initialized.has(name)) {
    const root = document.getElementById("section-dojo");
    root.innerHTML = Dojo.render();
    Dojo.init();
    Navigation.initialized.add(name);
  }
  if (name === "risk") {
    if (!Navigation.initialized.has(name)) { Risk.init(); Navigation.initialized.add(name); }
    else Risk.render();
  }
  if (name === "portfolio" && !Navigation.initialized.has(name)) {
    Portfolio.init();
    Navigation.initialized.add(name);
  }
  if (name === "diagnostic") Diagnostic.load();
  if (name === "alerts") { initAlerts(); refreshAlerts(); }
  if (name === "live") {
    // Charts initially created in a hidden section need their visible dimensions.
    requestAnimationFrame(() => { if (typeof layoutPanes === "function") layoutPanes(); });
  }
  try { localStorage.setItem("lastSection", name); } catch (_) {}
}

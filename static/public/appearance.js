// Apply appearance preferences and connect native document controls.
(() => {
  const root = document.documentElement;
  const key = "arctos.appearance.v1";
  const bannerKey = "arctos.page-banners.hidden.v1";
  const media = matchMedia("(prefers-color-scheme: dark)");
  let preferences = { palette: "california", mode: "system" };
  let hiddenBanners = [];
  let headerObserver = null;
  function read() {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "{}");
      preferences.palette = ["california", "neutral", "monokai"].includes(value.palette) ? value.palette : "california";
      preferences.mode = ["light", "dark", "system"].includes(value.mode) ? value.mode : "system";
      const banners = JSON.parse(localStorage.getItem(bannerKey) || "null");
      hiddenBanners = Array.isArray(banners) ? banners : localStorage.getItem("arctos.introduction.hidden.v1") === "true" ? ["events"] : [];
    } catch (_) { /* Blocked storage leaves the system default. */ }
  }
  function apply() {
    const scheme = preferences.mode === "system" ? (media.matches ? "dark" : "light") : preferences.mode;
    for (const target of [root, ...document.querySelectorAll("[data-public-theme]")]) {
      target.dataset.palette = preferences.palette;
      target.dataset.bsTheme = scheme;
    }
    for (const name of ["events", "leagues"]) {
      root.toggleAttribute(`data-public-${name}-hidden`, hiddenBanners.includes(name));
    }
    document.querySelectorAll("[data-public-preference]").forEach(control => {
      control.checked = preferences[control.dataset.publicPreference] === control.value;
      control.closest("label").classList.toggle("is-selected", control.checked);
    });
    document.querySelectorAll("[data-public-intro]").forEach(banner => {
      banner.hidden = hiddenBanners.includes(banner.dataset.publicIntro);
    });
    document.querySelectorAll("[data-public-intro-heading]").forEach(heading => {
      heading.hidden = !hiddenBanners.includes(heading.dataset.publicIntroHeading);
    });
    document.querySelectorAll("[data-public-intro-setting]").forEach(control => {
      control.checked = !hiddenBanners.includes(control.dataset.publicIntroSetting);
    });
  }
  function setBanner(name, hidden) {
    hiddenBanners = hiddenBanners.filter(value => value !== name);
    if (hidden) hiddenBanners.push(name);
    try { localStorage.setItem(bannerKey, JSON.stringify(hiddenBanners)); } catch (_) {}
    apply();
  }
  read(); apply();
  media.addEventListener("change", apply);
  window.addEventListener("storage", event => { if ([key, bannerKey].includes(event.key)) { read(); apply(); } });
  function connectDocument() {
    headerObserver?.disconnect();
    apply();
    const shell = document.querySelector(".public-shell");
    const header = shell?.querySelector(".app-header");
    if (header && !shell.hidden && window.ResizeObserver) {
      // Enlarged text can add header rows. Fragment targets need its actual
      // height to remain visible below the sticky navigation.
      headerObserver = new ResizeObserver(() => {
        const height = header.getBoundingClientRect().height;
        if (height > 0) shell.style.setProperty("--header-height", `${height}px`);
      });
      headerObserver.observe(header);
    }
    document.querySelectorAll("[data-public-preference]").forEach(control => {
      control.addEventListener("change", () => {
        preferences[control.dataset.publicPreference] = control.value;
        try {
          const value = JSON.stringify(preferences);
          localStorage.setItem(key, value);
          // A hidden application may already have mounted while its styles
          // load. Notify its existing listener in this document as well.
          window.dispatchEvent(new StorageEvent("storage", {
            key, newValue: value, storageArea: localStorage, url: location.href,
          }));
        } catch (_) {}
        apply();
      });
    });
    document.querySelectorAll("[data-public-intro-dismiss]").forEach(control => {
      control.hidden = false;
      control.addEventListener("click", () => {
        setBanner(control.dataset.publicIntroDismiss, true);
        document.getElementById(control.dataset.focusTarget || "page-content")?.focus();
      });
    });
    document.querySelectorAll("[data-public-intro-setting]").forEach(control => {
      control.addEventListener("change", () => setBanner(control.dataset.publicIntroSetting, !control.checked));
    });
    document.querySelectorAll("[data-public-utility]").forEach(menu => {
      const panel = menu.querySelector("#public-appearance");
      const links = menu.querySelector("[data-public-utility-links]");
      const trigger = menu.querySelector("[data-public-open-appearance]");
      const showPanel = show => { panel.hidden = !show; links.hidden = show; };
      trigger.hidden = false;
      trigger.addEventListener("click", () => { showPanel(true); panel.querySelector("input:checked")?.focus(); });
      menu.querySelector("[data-public-appearance-back]").addEventListener("click", () => { showPanel(false); trigger.focus(); });
      menu.querySelector("[data-public-appearance-close]").addEventListener("click", () => { menu.open = false; menu.querySelector("summary").focus(); });
      menu.addEventListener("toggle", () => { if (!menu.open) showPanel(false); });
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    connectDocument();
    document.addEventListener("click", event => {
      document.querySelectorAll(".public-shell details[open]").forEach(menu => {
        if (!menu.contains(event.target)) menu.open = false;
      });
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      const open = [...document.querySelectorAll(".public-shell details[open]")].at(-1);
      if (open) { open.open = false; open.querySelector("summary")?.focus(); event.preventDefault(); }
    });
    // This preference changes only subsequent Upcoming/Past calendar boundaries.
    try {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      document.cookie = `arctos_timezone=${encodeURIComponent(zone)}; Path=${root.dataset.publicPrefix || "/"}; SameSite=Lax; Max-Age=31536000${location.protocol === "https:" ? "; Secure" : ""}`;
    } catch (_) {}
  });
  window.addEventListener("arctos:public-document", connectDocument);
  window.addEventListener("arctos:application-visible", () => headerObserver?.disconnect(), {once: true});
})();

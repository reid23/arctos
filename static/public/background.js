// Keep public reading available while one application instance warms in place.
(() => {
  let host = document.getElementById("public-document");
  const main = document.getElementById("main");
  let status = document.getElementById("public-app-status");
  if (!host?.dataset.entry || !main || !status) return;
  const root = document.documentElement;
  const entry = host.dataset.entry;
  const prefix = (root.dataset.publicPrefix || "").replace(/\/$/, "");
  let started = false;
  let failed = false;
  let pending = null;
  let activated = false;
  let waitingForRoute = false;
  let finished = false;
  let openingTimer = null;
  let slowTimer = null;
  let frame = null;
  let trigger = null;
  let readingRequest = null;
  let renderedURL = location.href;
  let renderedTitle = document.title;
  let renamed = [];
  history.scrollRestoration = "manual";
  history.replaceState([scrollX, scrollY], "", location.href);

  function clearPending() {
    clearTimeout(openingTimer);
    clearTimeout(slowTimer);
    trigger?.removeAttribute("data-app-pending");
    trigger?.removeAttribute("aria-busy");
    trigger = null;
    pending = null;
    status.hidden = true;
  }

  function showRecovery(message, target) {
    status.hidden = false;
    status.classList.remove("visually-hidden");
    status.replaceChildren(message + " ");
    const retry = document.createElement("a");
    retry.href = target;
    retry.textContent = "Open page directly";
    retry.dataset.nativeNavigation = "true";
    status.append(retry);
    // Keep the recovery reachable from a clicked control below the viewport
    // without moving focus away from someone who has continued reading.
    if (document.activeElement === trigger) retry.focus();
  }

  function restorePublicIDs() {
    for (const [element, name, value] of renamed) element.setAttribute(name, value);
    renamed = [];
  }

  function fail() {
    if (finished || failed) return;
    failed = true;
    clearTimeout(openingTimer);
    clearTimeout(slowTimer);
    cancelAnimationFrame(frame);
    restorePublicIDs();
    // A failed hidden application cannot own IDs or the browser address while
    // the server document remains the usable page. Its retry is a native load.
    main.remove();
    observer.disconnect();
    // A detached Layout can still finish a queued title update. The visible
    // server document owns its title through later reading and Back/Forward.
    const titleObserver = new MutationObserver(() => {
      if (document.title !== renderedTitle) document.title = renderedTitle;
    });
    titleObserver.observe(document.head, {childList: true, characterData: true, subtree: true});
    if (activated) {
      history.replaceState([scrollX, scrollY], "", renderedURL);
      document.title = renderedTitle;
    }
    activated = false;
    trigger?.removeAttribute("data-app-pending");
    trigger?.removeAttribute("aria-busy");
    if (pending) showRecovery("This page could not open.", pending);
  }

  function handoff() {
    if (!activated || waitingForRoute || failed || finished || frame !== null) return;
    const content = main.querySelector("#app-layout > #page-content");
    const styles = [...main.querySelectorAll('link[rel="stylesheet"]')];
    if (!content?.childElementCount || !styles.length || styles.some(link => !link.sheet)) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!activated || waitingForRoute || failed || finished) return;
      const restoreFocus = host.contains(document.activeElement) || document.activeElement === document.body;
      finished = true;
      clearPending();
      readingRequest?.abort();
      host.remove();
      // These tags describe the server document. Layout owns the app title;
      // subsequent direct requests supply metadata for their own public URL.
      document.head.querySelectorAll("[data-public-meta]").forEach(element => element.remove());
      observer.disconnect();
      main.removeAttribute("aria-hidden");
      main.removeAttribute("inert");
      document.body.classList.remove("public-background", "public-document");
      document.removeEventListener("click", click);
      document.removeEventListener("submit", submit);
      document.removeEventListener("load", handoff, true);
      window.removeEventListener("popstate", back);
      window.removeEventListener("arctos:router-ready", navigate);
      window.removeEventListener("unhandledrejection", fail);
      window.removeEventListener("error", resourceError, true);
      if (restoreFocus) {
        // The browser applies the inherited visibility change at the frame
        // boundary. Focus the live route once it can receive focus, unless the
        // visitor has already selected a control in the revealed application.
        requestAnimationFrame(() => {
          if (document.activeElement === document.body) {
            main.querySelector("#app-layout > #page-content")?.focus({preventScroll: true});
          }
        });
      }
      window.dispatchEvent(new Event("arctos:application-visible"));
    });
  }
  const observer = new MutationObserver(() => {
    waitingForRoute = false;
    handoff();
  });
  observer.observe(main, {childList: true, subtree: true});

  function releasePublicIDs() {
    // The app's helpers query IDs globally. Preserve the visible document and
    // its accessible relationships until the new route is ready to replace it.
    const ids = new Set([...host.querySelectorAll("[id]")].map(element => element.id));
    for (const element of host.querySelectorAll("[id], [for], [aria-labelledby], [aria-describedby], [aria-controls], [data-focus-target], a[href^='#']")) {
      for (const name of ["id", "for", "aria-labelledby", "aria-describedby", "aria-controls", "data-focus-target", "href"]) {
        const value = element.getAttribute(name);
        if (!value) continue;
        const replacement = name === "href"
          ? (value.startsWith("#") && ids.has(value.slice(1)) ? "#public-retained-" + value.slice(1) : value)
          : value.split(" ").map(id => ids.has(id) ? "public-retained-" + id : id).join(" ");
        if (replacement !== value) {
          renamed.push([element, name, value]);
          element.setAttribute(name, replacement);
        }
      }
    }
  }

  function navigate() {
    if (failed || !pending || root.dataset.arctosRouterReady !== "true") return;
    readingRequest?.abort();
    clearTimeout(slowTimer);
    if (!activated) releasePublicIDs();
    const target = pending;
    waitingForRoute = target !== location.href || !main.querySelector("#app-layout > #page-content")?.childElementCount;
    const event = new CustomEvent("arctos:navigate", {detail: target, cancelable: true});
    window.dispatchEvent(event);
    if (!event.defaultPrevented) {
      restorePublicIDs();
      location.assign(target);
      return;
    }
    activated = true;
    clearTimeout(openingTimer);
    openingTimer = setTimeout(fail, 10000);
    handoff();
  }

  function resourceError(event) {
    const target = event.target;
    if (target?.tagName === "LINK" && target.rel === "stylesheet" && main.contains(target)) fail();
  }

  function start() {
    if (started) return;
    started = true;
    import(entry).catch(fail);
  }

  function destination(value) {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || !["http:", "https:"].includes(url.protocol)) return null;
    if (prefix && url.pathname !== prefix && !url.pathname.startsWith(prefix + "/")) return null;
    const path = url.pathname.slice(prefix.length) || "/";
    if (/^\/(?:_api|api|static|assets|waivers)(?:\/|$)/.test(path)) return null;
    if (path === "/scoreboard-render" || /\/(?:scoreboard|enter-results)$/.test(path)) return null;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return null;
    return url.href;
  }

  function markPending(source, message) {
    clearPending();
    trigger = source;
    trigger?.setAttribute("data-app-pending", "true");
    trigger?.setAttribute("aria-busy", "true");
    status.classList.add("visually-hidden");
    status.hidden = false;
    status.textContent = message;
  }

  function openApplication(target, source) {
    readingRequest?.abort();
    markPending(source, "Opening page…");
    pending = target;
    if (failed) {
      trigger?.removeAttribute("data-app-pending");
      trigger?.removeAttribute("aria-busy");
      showRecovery("This page could not open.", target);
      return;
    }
    slowTimer = setTimeout(() => {
      if (pending && !activated) showRecovery("The app is still starting.", pending);
    }, 5000);
    start();
    navigate();
  }

  function updateMetadata(next) {
    document.title = next.title;
    for (const selector of ['meta[name="description"]', 'meta[name="robots"]', 'link[rel="canonical"]',
      'meta[property="og:title"]', 'meta[property="og:description"]', 'meta[property="og:type"]', 'meta[property="og:url"]']) {
      document.head.querySelector(selector)?.remove();
      const replacement = next.head.querySelector(selector);
      if (replacement) document.head.append(replacement.cloneNode(true));
    }
  }

  async function readPage(target, source, position = null) {
    readingRequest?.abort();
    const controller = new AbortController();
    readingRequest = controller;
    markPending(source, "Opening page…");
    try {
      const response = await fetch(target, {signal: controller.signal, credentials: "same-origin", cache: "no-store"});
      if (!response.ok || response.status !== 200 || new URL(response.url).origin !== location.origin
          || !response.headers.get("content-type")?.includes("text/html")) throw new Error("Document unavailable");
      const next = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = next.getElementById("public-document");
      if (controller.signal.aborted) return;
      if (!replacement?.querySelector("#public-app-status")
          || next.documentElement.dataset.publicPrefix !== root.dataset.publicPrefix
          || next.documentElement.dataset.publicBackground !== "true" || replacement?.dataset.entry !== entry
          || !replacement.querySelector("main[data-public-page]")) {
        if (position === null) location.assign(target);
        else location.replace(target);
        return;
      }
      replacement.querySelectorAll("script").forEach(script => script.remove());
      clearPending();
      if (position === null) {
        const destinationURL = new URL(response.url);
        destinationURL.hash = new URL(target).hash;
        history.replaceState([scrollX, scrollY], "", location.href);
        if (destinationURL.href !== location.href) history.pushState([0, 0], "", destinationURL.href);
      }
      host.replaceWith(replacement);
      host = replacement;
      status = host.querySelector("#public-app-status");
      renderedURL = location.href;
      updateMetadata(next);
      renderedTitle = document.title;
      window.dispatchEvent(new Event("arctos:public-document"));
      const content = host.querySelector("main");
      content?.focus({preventScroll: true});
      if (position) scrollTo(position[0], position[1]);
      else {
        scrollTo(0, 0);
        scrollToFragment();
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      trigger?.removeAttribute("data-app-pending");
      trigger?.removeAttribute("aria-busy");
      // Back has already changed the address; use a real document navigation
      // if its content cannot be restored, so URL and visible page stay aligned.
      if (position !== null) location.reload();
      else showRecovery("This page could not open.", target);
    }
  }

  function open(target, source, reading) {
    if (!activated && reading && (failed || root.dataset.arctosRouterReady !== "true")) readPage(target, source);
    else openApplication(target, source);
  }

  function click(event) {
    const link = event.target.closest?.("a[href]");
    if (!host.contains(link) || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        || link.hasAttribute("download") || link.hasAttribute("data-native-navigation") || (link.target && link.target !== "_self")) return;
    const target = destination(link.href);
    if (!target) return;
    event.preventDefault();
    open(target, link, link.hasAttribute("data-public-reading"));
  }

  function submit(event) {
    const form = event.target;
    if (!host.contains(form) || event.defaultPrevented || form.method.toLowerCase() !== "get" || (form.target && form.target !== "_self")) return;
    const url = new URL(form.action, location.href);
    url.search = new URLSearchParams(new FormData(form, event.submitter)).toString();
    const target = destination(url.href);
    if (!target) return;
    event.preventDefault();
    open(target, event.submitter || form.querySelector('[type="submit"]'), form.hasAttribute("data-public-reading"));
  }

  function scrollToFragment() {
    try {
      if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
    } catch (_) { /* Invalid fragments leave the page at its normal position. */ }
  }

  function back(event) {
    if (activated || finished) return;
    const previous = new URL(renderedURL);
    if (previous.pathname === location.pathname && previous.search === location.search) {
      clearPending();
      readingRequest?.abort();
      if (location.hash) scrollToFragment();
      else if (Array.isArray(event.state)) scrollTo(event.state[0], event.state[1]);
      return;
    }
    readPage(location.href, null, Array.isArray(event.state) ? event.state : [0, 0]);
  }

  document.addEventListener("click", click);
  document.addEventListener("submit", submit);
  document.addEventListener("load", handoff, true);
  window.addEventListener("popstate", back);
  window.addEventListener("arctos:router-ready", navigate);
  window.addEventListener("unhandledrejection", fail);
  window.addEventListener("error", resourceError, true);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (window.requestIdleCallback) window.requestIdleCallback(start, {timeout: 1500});
    else setTimeout(start, 0);
  }));
})();

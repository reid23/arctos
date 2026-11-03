// Keep the startup message visible until the application can take over.
(() => {
  const boot = document.getElementById("app-boot");
  if (!boot || !boot.dataset.entry) return;
  const startup = document.getElementById("app-startup") || boot;
  const main = document.getElementById("main");
  if (!main) return;
  const status = boot.querySelector("[data-boot-message]");
  const retry = boot.querySelector("[data-boot-retry]");
  let finished = false;
  let failed = false;
  let handoffFrame = null;

  function stopListening() {
    clearTimeout(slow);
    clearTimeout(recovery);
    cancelAnimationFrame(handoffFrame);
    observer.disconnect();
    window.removeEventListener("unhandledrejection", fail);
    window.removeEventListener("error", resourceError, true);
    document.removeEventListener("load", inspect, true);
  }

  function fail() {
    if (finished || failed) return;
    failed = true;
    boot.dataset.bootState = "failed";
    status.textContent = `${boot.dataset.workflow} could not open. Please try again.`;
    retry.hidden = false;
    stopListening();
  }

  function ready() {
    if (finished || failed) return;
    finished = true;
    stopListening();
    boot.dataset.bootState = "ready";
    // Layout is ready to own its route loading/content/error states. Hide the
    // temporary chrome in the same task so there is never a visible second header.
    const restoreFocus = startup.contains(document.activeElement);
    boot.hidden = true;
    startup.hidden = true;
    main.removeAttribute("aria-hidden");
    main.removeAttribute("inert");
    document.body.classList.remove("public-startup");
    if (restoreFocus) {
      // Let the reveal paint before focusing the live route. A hidden startup
      // control can retain focus until that paint; preserve any application
      // control the visitor has selected in the meantime.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (document.activeElement === document.body || startup.contains(document.activeElement)) {
          main.querySelector("#app-layout > #page-content")?.focus({preventScroll: true});
        }
      }));
    }
    window.dispatchEvent(new Event("arctos:application-visible"));
  }

  function inspect() {
    if (finished || failed) return;
    // The route owns its data loading and error states after this handoff.
    const content = main.querySelector("#app-layout > #page-content");
    if (!content?.childElementCount) return;
    // Application styles can finish after its first route renders. Keep the
    // startup page visible until those styles are ready for the next paint.
    const styles = [...main.querySelectorAll('link[rel="stylesheet"]')];
    if (!styles.length || styles.some(link => !link.sheet) || handoffFrame !== null) return;
    handoffFrame = requestAnimationFrame(() => {
      handoffFrame = null;
      ready();
    });
  }

  function resourceError(event) {
    const target = event.target;
    const scriptFailed = target?.tagName === "SCRIPT" || event.filename?.includes("/assets/");
    const stylesheetFailed = target?.tagName === "LINK"
      && target.rel === "stylesheet" && main.contains(target);
    if (scriptFailed || stylesheetFailed) fail();
  }

  const slow = setTimeout(() => {
    if (finished || failed) return;
    boot.dataset.bootState = "slow";
    status.textContent = "Still loading. This is taking longer than usual. You can go back while it finishes.";
  }, 5000);
  const recovery = setTimeout(() => {
    if (finished || failed) return;
    retry.hidden = false;
  }, 20000);
  const observer = new MutationObserver(inspect);
  observer.observe(main, {childList: true, subtree: true, attributes: true});
  document.addEventListener("load", inspect, true);
  window.addEventListener("unhandledrejection", fail);
  window.addEventListener("error", resourceError, true);
  // The generated entry starts Wasm itself. The rendered route and its styles
  // determine readiness; import completion alone does not.
  import(boot.dataset.entry).catch(fail);
})();

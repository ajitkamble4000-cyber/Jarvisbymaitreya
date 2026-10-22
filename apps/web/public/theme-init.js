// Applies the viewer's remembered theme before first paint. Dark is the default.
// Loaded as a same-origin classic script so that the nginx CSP (script-src 'self') allows it.
// It is only a per-viewer convenience: the settings API remains the source of truth.
(function () {
  try {
    var stored = JSON.parse(localStorage.getItem("jarvis.ui") || "{}");
    var theme = stored && stored.theme;
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.setAttribute("data-theme", "dark");
  } catch (error) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();

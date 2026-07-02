// Initialise Mermaid and re-render diagrams on Material's instant navigation.
// Material for MkDocs loads pages via XHR (instant loading), so we must
// re-run Mermaid after each page swap, not just on first load.

(function () {
  function render() {
    if (typeof mermaid === "undefined") return;
    mermaid.initialize({
      startOnLoad: false,
      theme: (document.body.getAttribute("data-md-color-scheme") === "slate")
        ? "dark"
        : "default",
      securityLevel: "loose",
      flowchart: { htmlLabels: true, curve: "basis" }
    });
    mermaid.run({ querySelector: ".mermaid" });
  }

  // First load
  document.addEventListener("DOMContentLoaded", render);

  // Material for MkDocs instant navigation hook (if available)
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(render);
  }
})();

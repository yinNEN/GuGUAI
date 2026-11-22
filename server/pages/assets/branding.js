(function () {
  function darken(hex, amount) {
    var v = hex.replace("#", "");
    var nums = [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
    return "#" + nums.map(function (n) {
      return Math.max(0, Math.round(n * (1 - amount))).toString(16).padStart(2, "0");
    }).join("");
  }
  function escapeXml(value) {
    return String(value).replace(/[<>&"']/g, function (char) {
      return { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[char];
    });
  }
  function safeIconUrl(data) {
    return typeof data.mark_image === "string" && data.mark_image.indexOf("/api/public/branding/icon") === 0 ? data.mark_image : "";
  }
  function applyFavicon(data, mark, color) {
    var link = document.querySelector('link[rel~="icon"]');
    if (data.mark_mode === "none") {
      if (link) link.parentNode.removeChild(link);
      return;
    }
    var href = "";
    var type = "";
    if (data.mark_mode === "image" && safeIconUrl(data)) {
      href = safeIconUrl(data);
    } else {
      var text = escapeXml(mark || "G");
      var size = text.length > 2 ? 20 : 30;
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" rx="11" fill="' + color + '"/><text x="24" y="25" text-anchor="middle" dominant-baseline="middle" fill="#fff" font-family="system-ui,sans-serif" font-size="' + size + '" font-weight="600">' + text + '</text></svg>';
      href = "data:image/svg+xml," + encodeURIComponent(svg);
      type = "image/svg+xml";
    }
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.type = type;
    link.href = href;
  }
  function apply(data) {
    var title = (data.title || "GuGuAI").slice(0, 80);
    var subtitle = (data.subtitle || "").slice(0, 120);
    var mark = (data.mark || "G").slice(0, 4);
    var color = /^#[0-9a-fA-F]{6}$/.test(data.color || "") ? data.color : "#4d6bfe";
    document.querySelectorAll("[data-brand-name]").forEach(function (el) {
      el.textContent = title + (el.getAttribute("data-brand-suffix") || "");
    });
    document.querySelectorAll("[data-brand-subtitle]").forEach(function (el) {
      el.textContent = subtitle;
    });
    document.querySelectorAll("[data-brand-mark]").forEach(function (el) {
      if (data.mark_mode === "none") {
        el.style.display = "none";
        return;
      }
      el.style.display = "";
      el.style.backgroundImage = "";
      if (data.mark_mode === "image" && safeIconUrl(data)) {
        el.textContent = "";
        el.style.backgroundImage = "url(" + JSON.stringify(safeIconUrl(data)) + ")";
        el.style.backgroundSize = "cover";
        el.style.backgroundPosition = "center";
        el.style.fontSize = "";
        return;
      }
      el.textContent = mark;
      el.style.fontSize = mark.length > 2 ? "12px" : "";
    });
    document.title = document.title.replace(/GuGuAI/g, title);
    applyFavicon(data, mark, color);
    var root = document.documentElement;
    root.style.setProperty("--brand", color);
    root.style.setProperty("--brand-dark", darken(color, 0.14));
    root.style.setProperty("--brand-soft", color + "22");
  }
  fetch("/api/public/branding", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { if (data) apply(data); })
    .catch(function () {});
})();

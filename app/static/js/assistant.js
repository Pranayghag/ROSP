/*
 * CampusCare Assistant.
 *
 * A thin client: it renders messages and posts questions. Every answer is
 * composed on the server, where the current user's identity is known and each
 * lookup can be scoped to them. Nothing here decides what a student is allowed
 * to see.
 *
 * The transcript lives in this tab only. Closing it discards the conversation.
 */
(function () {
  "use strict";

  var root = document.getElementById("assistant");
  if (!root) return;

  var launcher = document.getElementById("assistant-launcher");
  var panel = document.getElementById("assistant-panel");
  var closeButton = document.getElementById("assistant-close");
  var log = document.getElementById("assistant-log");
  var suggestionBar = document.getElementById("assistant-suggestions");
  var form = document.getElementById("assistant-form");
  var input = document.getElementById("assistant-input");

  var opened = false;
  var busy = false;

  // ------------------------------------------------------------ rendering

  /**
   * Render the assistant's light markup: **bold**, bullet lines, paragraphs.
   *
   * Text is escaped first and only these three patterns are re-introduced, so
   * a complaint title containing HTML cannot inject anything.
   */
  function render(text) {
    var escaped = document.createElement("div");
    escaped.textContent = text;
    var safe = escaped.innerHTML;

    safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

    return safe
      .split("\n")
      .map(function (line) {
        var trimmed = line.trim();
        if (!trimmed) return "";
        if (trimmed.charAt(0) === "•") {
          return '<div class="assistant-bullet">' + trimmed + "</div>";
        }
        if (/^\d+\.\s/.test(trimmed)) {
          return '<div class="assistant-bullet">' + trimmed + "</div>";
        }
        return "<p>" + trimmed + "</p>";
      })
      .join("");
  }

  function addMessage(who, text, links) {
    var row = document.createElement("div");
    row.className = "assistant-message assistant-" + who;

    var bubble = document.createElement("div");
    bubble.className = "assistant-bubble";
    bubble.innerHTML = render(text);

    (links || []).forEach(function (link) {
      var anchor = document.createElement("a");
      anchor.className = "assistant-link";
      anchor.href = link.url;
      anchor.textContent = link.label + " →";
      bubble.appendChild(anchor);
    });

    row.appendChild(bubble);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function showTyping() {
    var row = document.createElement("div");
    row.className = "assistant-message assistant-bot assistant-typing-row";
    row.innerHTML =
      '<div class="assistant-bubble assistant-typing">' +
      "<span></span><span></span><span></span></div>";
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function setSuggestions(items) {
    suggestionBar.innerHTML = "";
    (items || []).forEach(function (item) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "assistant-chip";
      chip.textContent = item;
      chip.addEventListener("click", function () {
        send(item);
      });
      suggestionBar.appendChild(chip);
    });
  }

  // -------------------------------------------------------------- network

  function present(answer) {
    addMessage("bot", answer.text, answer.links);
    setSuggestions(answer.suggestions);
  }

  function failed() {
    addMessage(
      "bot",
      "I could not reach the server just then. Check your connection and try " +
        "again — your complaints are unaffected."
    );
  }

  function send(message) {
    var text = (message || input.value || "").trim();
    if (!text || busy) return;

    busy = true;
    input.value = "";
    setSuggestions([]);
    addMessage("user", text);

    var typing = showTyping();

    fetch(root.dataset.askUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": root.dataset.csrf
      },
      body: JSON.stringify({ message: text })
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (answer) {
        typing.remove();
        if (answer) present(answer);
        else failed();
      })
      .catch(function () {
        typing.remove();
        failed();
      })
      .finally(function () {
        busy = false;
        input.focus();
      });
  }

  // --------------------------------------------------------------- panel

  function open() {
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    root.classList.add("is-open");
    input.focus();

    if (opened) return;
    opened = true;

    var typing = showTyping();
    fetch(root.dataset.openUrl)
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (answer) {
        typing.remove();
        if (answer) present(answer);
        else failed();
      })
      .catch(function () {
        typing.remove();
        failed();
      });
  }

  function close() {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    root.classList.remove("is-open");
    launcher.focus();
  }

  launcher.addEventListener("click", function () {
    if (panel.hidden) open();
    else close();
  });

  closeButton.addEventListener("click", close);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) close();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send();
  });
})();

/*
 * Evidence upload: drag-and-drop, previews, removal, and advisory suggestions.
 *
 * IMPORTANT: everything here is a convenience for the person filling in the
 * form. It is not a security control. The server re-validates every file --
 * size, extension, declared MIME type and the actual image bytes -- in
 * app/services/uploads.py. Never move a check *out* of the server and into
 * this file.
 */
(function () {
  "use strict";

  var config = window.ROSP_UPLOAD_CONFIG || {};
  var maxFiles = config.maxFiles || 5;
  var maxBytes = (config.maxFileSizeMb || 5) * 1024 * 1024;
  var allowedTypes = config.allowedTypes || [];
  var allowedExtensions = config.allowedExtensions || [];

  // Element ids are configurable so the same widget serves both the student's
  // evidence upload and the staff member's resolution photos. Defaults match
  // the complaint submission form.
  var input = document.getElementById(config.inputId || "photos");
  var dropzone = document.getElementById(config.dropzoneId || "dropzone");
  var grid = document.getElementById(config.gridId || "preview-grid");
  var counter = document.getElementById(config.counterId || "photo-counter");
  var errorBox = document.getElementById(config.errorsId || "photo-errors");
  var clearButton = document.getElementById(config.clearId || "clear-photos");

  if (!input || !grid) {
    return;
  }

  /** Files chosen so far. This is the source of truth; input.files mirrors it. */
  var selected = [];
  /** Object URLs currently in use, so they can be revoked and not leak memory. */
  var previewUrls = new Map();

  // ---------------------------------------------------------------- helpers

  function formatSize(bytes) {
    var kb = bytes / 1024;
    return kb < 1024 ? Math.round(kb) + " KB" : (kb / 1024).toFixed(1) + " MB";
  }

  function extensionOf(name) {
    var dot = name.lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot).toLowerCase();
  }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
  }

  function showErrors(messages) {
    if (!errorBox) return;
    var unique = messages.filter(function (message, index, all) {
      return all.indexOf(message) === index;
    });

    if (!unique.length) {
      errorBox.classList.add("d-none");
      errorBox.textContent = "";
      return;
    }
    errorBox.classList.remove("d-none");
    errorBox.innerHTML = unique
      .map(function (message) {
        return "<div>" + escapeHtml(message) + "</div>";
      })
      .join("");
  }

  /**
   * Reject files the server would reject anyway, so the student finds out
   * immediately instead of after an upload.
   */
  function validate(file) {
    if (allowedTypes.length && allowedTypes.indexOf(file.type) === -1) {
      return file.name + " is not a JPG, PNG or WEBP image.";
    }
    if (allowedExtensions.length && allowedExtensions.indexOf(extensionOf(file.name)) === -1) {
      return file.name + " does not have a supported file extension.";
    }
    if (file.size > maxBytes) {
      return (
        file.name + " is " + formatSize(file.size) + ", over the " +
        config.maxFileSizeMb + " MB limit."
      );
    }
    if (file.size === 0) {
      return file.name + " is empty.";
    }
    return null;
  }

  /** Copy `selected` back into the real file input so the form submits it. */
  function syncInput() {
    var transfer = new DataTransfer();
    selected.forEach(function (file) {
      transfer.items.add(file);
    });
    input.files = transfer.files;
  }

  function isDuplicate(file) {
    return selected.some(function (existing) {
      return existing.name === file.name &&
        existing.size === file.size &&
        existing.lastModified === file.lastModified;
    });
  }

  // ----------------------------------------------------------------- render

  var CLOSE_ICON =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.5" stroke-linecap="round" aria-hidden="true">' +
    '<path d="M18 6L6 18M6 6l12 12"/></svg>';

  function render() {
    grid.innerHTML = "";

    selected.forEach(function (file, index) {
      var url = previewUrls.get(file);
      if (!url) {
        url = URL.createObjectURL(file);
        previewUrls.set(file, url);
      }

      var card = document.createElement("div");
      card.className = "preview-card";

      var image = document.createElement("img");
      image.src = url;
      image.alt = "Preview of " + file.name;
      image.className = "preview-image";

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "preview-remove";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.title = "Remove this photo";
      remove.innerHTML = CLOSE_ICON;
      remove.addEventListener("click", function () {
        removeAt(index);
      });

      var name = document.createElement("div");
      name.className = "preview-name";
      name.textContent = file.name;
      name.title = file.name + " (" + formatSize(file.size) + ")";

      card.appendChild(image);
      card.appendChild(remove);
      card.appendChild(name);
      grid.appendChild(card);
    });

    grid.classList.toggle("d-none", selected.length === 0);

    if (clearButton) {
      clearButton.classList.toggle("d-none", selected.length === 0);
    }
    if (counter) {
      counter.textContent = selected.length + " of " + maxFiles + " selected";
    }
    if (dropzone) {
      dropzone.classList.toggle("is-full", selected.length >= maxFiles);
    }
    syncInput();
  }

  function removeAt(index) {
    var file = selected[index];
    if (file && previewUrls.has(file)) {
      URL.revokeObjectURL(previewUrls.get(file));
      previewUrls.delete(file);
    }
    selected.splice(index, 1);
    render();
  }

  function removeAll() {
    previewUrls.forEach(function (url) {
      URL.revokeObjectURL(url);
    });
    previewUrls.clear();
    selected = [];
    render();
    showErrors([]);
  }

  /** Add a list of files, reporting anything rejected. */
  function accept(files) {
    var errors = [];

    Array.prototype.slice.call(files || []).forEach(function (file) {
      if (selected.length >= maxFiles) {
        errors.push("You can attach at most " + maxFiles + " photos.");
        return;
      }
      if (isDuplicate(file)) {
        errors.push(file.name + " has already been added.");
        return;
      }
      var problem = validate(file);
      if (problem) {
        errors.push(problem);
        return;
      }
      selected.push(file);
    });

    showErrors(errors);
    render();
  }

  // ---------------------------------------------------------------- wiring

  input.addEventListener("change", function () {
    accept(input.files);
    // The input is only a transport; `selected` owns the list, and syncInput()
    // rewrites input.files from it during render().
  });

  if (dropzone) {
    dropzone.addEventListener("click", function () {
      if (selected.length < maxFiles) input.click();
    });

    dropzone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (selected.length < maxFiles) input.click();
      }
    });

    ["dragenter", "dragover"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        event.stopPropagation();
        dropzone.classList.add("is-dragover");
      });
    });

    ["dragleave", "drop"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        event.stopPropagation();
        dropzone.classList.remove("is-dragover");
      });
    });

    dropzone.addEventListener("drop", function (event) {
      if (event.dataTransfer && event.dataTransfer.files) {
        accept(event.dataTransfer.files);
      }
    });
  }

  // Dropping a file anywhere else on the page would make the browser navigate
  // away from a half-filled form, losing the student's work.
  ["dragover", "drop"].forEach(function (name) {
    window.addEventListener(name, function (event) {
      if (!dropzone || !dropzone.contains(event.target)) {
        event.preventDefault();
      }
    });
  });

  if (clearButton) {
    clearButton.addEventListener("click", removeAll);
  }

  // --------------------------------------------------- advisory suggestions

  /*
   * Suggestions are hints only. Nothing is applied unless the student clicks
   * "Use this" -- that click is the validation step the specification requires.
   */
  var titleField = document.getElementById("title");
  var descriptionField = document.getElementById("description");
  var categoryField = document.getElementById("category_id");
  var categoryHint = document.getElementById("category-hint");
  var priorityHint = document.getElementById("priority-hint");

  function renderHint(hintBox, suggestion, apply) {
    if (!hintBox) return;

    if (!suggestion || !suggestion.value) {
      hintBox.classList.add("d-none");
      hintBox.innerHTML = "";
      return;
    }

    hintBox.classList.remove("d-none");
    hintBox.innerHTML = "";

    var label = document.createElement("span");
    label.innerHTML =
      "Suggestion: <strong>" + escapeHtml(suggestion.value) + "</strong>" +
      (suggestion.reason
        ? " <span class='text-muted'>(" + escapeHtml(suggestion.reason) + ")</span>"
        : "");

    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-ghost btn-sm py-0 px-1";
    button.textContent = "Use this";
    button.addEventListener("click", function () {
      apply(suggestion.value);
      hintBox.classList.add("d-none");
    });

    hintBox.appendChild(label);
    hintBox.appendChild(button);
  }

  var suggestTimer = null;

  function requestSuggestions() {
    if (!config.suggestUrl || !titleField || !descriptionField) return;

    var title = titleField.value.trim();
    var description = descriptionField.value.trim();
    if (title.length + description.length < 12) return;

    fetch(config.suggestUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": config.csrfToken || ""
      },
      body: JSON.stringify({ title: title, description: description })
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data) return;

        renderHint(categoryHint, data.category, function () {
          if (categoryField && data.category.id) {
            categoryField.value = String(data.category.id);
          }
        });

        renderHint(priorityHint, data.priority, function (value) {
          var radio = document.getElementById("priority-" + value);
          if (radio) radio.checked = true;
        });
      })
      .catch(function () {
        /* Suggestions are optional; a failure must never block submission. */
      });
  }

  function scheduleSuggestions() {
    window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(requestSuggestions, 600);
  }

  if (titleField) titleField.addEventListener("input", scheduleSuggestions);
  if (descriptionField) descriptionField.addEventListener("input", scheduleSuggestions);
})();

/**
 * Context Picker for chat_next.
 *
 * Provides a VSCode-style dropdown for selecting tools, libraries, folders,
 * and documents as context hints. Selected items appear as pills in the
 * toolbar and are submitted as JSON in the hidden #context-hints field.
 */
(function () {
  const contextButton = document.getElementById("context-button");
  const picker = document.getElementById("context-picker");
  const searchInput = document.getElementById("context-picker-search");
  const resultsContainer = document.getElementById("context-picker-results");
  const pillsContainer = document.getElementById("context-pills-container");
  const hiddenField = document.getElementById("context-hints");
  const promptForm = document.getElementById("prompt-form");

  if (!contextButton || !picker || !pillsContainer) return;

  // Currently selected context items: [{type, id, name, description, icon}, ...]
  let selectedItems = [];

  // Autocomplete data cache
  let cachedItems = null;
  let fetchController = null;

  // Quick-select mode: opened via @ shortcut, first selection closes picker
  let quickSelectMode = false;

  // ========================================================================
  // Prevent focus theft — stop clicks inside the picker from bubbling
  // to external focusin handlers (e.g. chatInput.js)
  // ========================================================================

  picker.addEventListener("mousedown", function (e) {
    // Prevent the textarea from stealing focus when clicking inside the picker
    e.stopPropagation();
  });

  picker.addEventListener("click", function (e) {
    e.stopPropagation();
  });

  // ========================================================================
  // Picker open / close
  // ========================================================================

  function openPicker(options) {
    quickSelectMode = !!(options && options.quickSelect);
    picker.classList.remove("d-none");
    // Small delay to ensure the element is visible before focusing
    requestAnimationFrame(() => {
      searchInput.value = "";
      searchInput.focus();
    });
    fetchItems("");
    // Use setTimeout so the opening click itself doesn't trigger the close
    setTimeout(() => {
      document.addEventListener("mousedown", outsideClickHandler, true);
    }, 0);
    document.addEventListener("keydown", escapeHandler, true);
  }

  function closePicker() {
    picker.classList.add("d-none");
    quickSelectMode = false;
    document.removeEventListener("mousedown", outsideClickHandler, true);
    document.removeEventListener("keydown", escapeHandler, true);
    focusChatPrompt();
  }

  function outsideClickHandler(e) {
    if (!picker.contains(e.target) && e.target !== contextButton && !contextButton.contains(e.target)) {
      closePicker();
    }
  }

  function escapeHandler(e) {
    if (e.key === "Escape") {
      closePicker();
      e.stopPropagation();
    }
  }

  contextButton.addEventListener("click", function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (picker.classList.contains("d-none")) {
      openPicker();
    } else {
      closePicker();
    }
  });

  // Close button inside the picker header
  const closeButton = document.getElementById("context-picker-close");
  if (closeButton) {
    closeButton.addEventListener("mousedown", function (e) {
      e.preventDefault();
      e.stopPropagation();
    });
    closeButton.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      closePicker();
    });
  }

  // ========================================================================
  // Fetch autocomplete items
  // ========================================================================

  async function fetchItems(query) {
    if (fetchController) fetchController.abort();
    fetchController = new AbortController();

    const url = `/chat_next/id/${chat_id}/context_autocomplete/?q=${encodeURIComponent(query)}`;
    try {
      resultsContainer.innerHTML = '<div class="context-picker-loading text-center text-muted py-3"><span class="spinner-border spinner-border-sm"></span></div>';
      const resp = await fetch(url, {signal: fetchController.signal});
      const data = await resp.json();
      cachedItems = data.items || [];
      renderResults(cachedItems);
    } catch (err) {
      if (err.name !== "AbortError") {
        console.error("Context autocomplete error:", err);
        resultsContainer.innerHTML = `<div class="text-muted text-center py-3">${TYPE_LABELS.loadError || "Error loading items"}</div>`;
      }
    }
  }

  // Debounced search
  let searchTimeout = null;
  searchInput.addEventListener("input", function (e) {
    e.stopPropagation();
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => fetchItems(searchInput.value.trim()), 200);
  });

  // ========================================================================
  // Icon mapping
  // ========================================================================

  const TYPE_ICONS = {
    tool: "bi-tools",
    library: "bi-collection",
    folder: "bi-folder",
    document: "bi-file-earmark-text",
    skill: "bi-lightbulb",
  };

  const TYPE_LABELS = window.CONTEXT_PICKER_LABELS || {
    tool: "Tools",
    library: "Libraries",
    folder: "Folders",
    document: "Documents",
    skill: "Skills",
  };

  function getIcon(item) {
    // Tools may supply a custom icon from the API
    if (item.icon) return "bi-" + item.icon;
    return TYPE_ICONS[item.type] || "bi-circle";
  }

  // ========================================================================
  // Render results grouped by type
  // ========================================================================

  function renderResults(items) {
    const selectedKeys = new Set(selectedItems.map(i => `${i.type}:${i.id}`));

    if (items.length === 0) {
      resultsContainer.innerHTML = `<div class="text-muted text-center py-3">${TYPE_LABELS.noItems || "No items found"}</div>`;
      return;
    }

    // Group by type
    const groups = {};
    for (const item of items) {
      if (!groups[item.type]) groups[item.type] = [];
      groups[item.type].push(item);
    }

    let html = "";
    const typeOrder = ["skill", "tool", "library", "folder", "document"];
    for (const type of typeOrder) {
      if (!groups[type]) continue;
      html += `<div class="context-picker-group">`;
      html += `<div class="context-picker-group-label">${TYPE_LABELS[type] || type}</div>`;
      for (const item of groups[type]) {
        const icon = getIcon(item);
        const iconAttr = item.icon ? item.icon : "";
        const key = `${item.type}:${item.id}`;
        const isSelected = selectedKeys.has(key);
        const titleText = item.description ? `${item.name} — ${item.description}` : item.name;
        const disabledTool = item.type === "tool" && item.enabled === false;
        html += `<button type="button" class="context-picker-item${isSelected ? " selected" : ""}" data-type="${item.type}" data-id="${item.id}" data-name="${escapeAttr(item.name)}" data-description="${escapeAttr(item.description || "")}" data-icon="${escapeAttr(iconAttr)}" title="${escapeAttr(titleText)}">`;
        html += `<i class="bi ${icon} me-2"></i>`;
        html += `<span class="context-picker-item-name">${escapeHtml(item.name)}</span>`;
        if (disabledTool) {
          html += `<span class="badge bg-secondary ms-2" style="font-size:0.65em">off</span>`;
        }
        if (item.description) {
          html += `<span class="context-picker-item-desc ms-2 text-muted">${escapeHtml(item.description)}</span>`;
        }
        html += `</button>`;
      }
      html += `</div>`;
    }

    resultsContainer.innerHTML = html;

    // Click handlers — toggle selection
    resultsContainer.querySelectorAll(".context-picker-item").forEach(btn => {
      btn.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        const itemData = {
          type: this.dataset.type,
          id: this.dataset.id,
          name: this.dataset.name,
          description: this.dataset.description,
          icon: this.dataset.icon || "",
        };
        const key = `${itemData.type}:${itemData.id}`;
        if (selectedKeys.has(key)) {
          // Deselect
          removeItem(itemData.type, itemData.id);
          this.classList.remove("selected");
          selectedKeys.delete(key);
        } else {
          // Select
          addItem(itemData);
          this.classList.add("selected");
          selectedKeys.add(key);
        }
        // In quick-select mode (@ shortcut), close picker after first selection
        if (quickSelectMode) {
          closePicker();
          return;
        }
        // Keep focus on the item for keyboard users; only refocus search for mouse
        if (!e.detail || e.detail === 0) {
          // Triggered programmatically (keyboard Enter) — keep focus on item
          this.focus();
        } else {
          searchInput.focus();
        }
      });
    });
  }

  // ========================================================================
  // Pills management (in toolbar, next to the @ button)
  // ========================================================================

  function addItem(item) {
    const key = `${item.type}:${item.id}`;
    if (selectedItems.some(i => `${i.type}:${i.id}` === key)) return;
    selectedItems.push(item);
    renderPills();
    syncHiddenField();
  }

  function removeItem(type, id) {
    selectedItems = selectedItems.filter(i => !(i.type === type && String(i.id) === String(id)));
    renderPills();
    syncHiddenField();
  }

  function renderPills() {
    if (selectedItems.length === 0) {
      pillsContainer.innerHTML = "";
      if (typeof resizeOtherElements === "function") resizeOtherElements();
      return;
    }

    let html = "";
    for (const item of selectedItems) {
      const icon = item.icon ? ("bi-" + item.icon) : (TYPE_ICONS[item.type] || "bi-circle");
      const titleText = item.description ? `${item.name} — ${item.description}` : item.name;
      const openUrl = typeof window.buildContextHintLibrarianUrl === "function"
        ? window.buildContextHintLibrarianUrl(chat_id, item)
        : "";
      const openableClass = openUrl ? " context-pill-openable" : "";
      const openableAttrs = openUrl
        ? ` data-open-url="${escapeAttr(openUrl)}" role="button" tabindex="0" aria-label="${escapeAttr(titleText)}"`
        : "";
      html += `<span class="context-pill${openableClass}" data-type="${item.type}" data-id="${item.id}" title="${escapeAttr(titleText)}"${openableAttrs}>`;
      html += `<span class="context-pill-icon"><i class="bi ${icon}"></i></span>`;
      html += `<button type="button" class="context-pill-remove" aria-label="Remove"><i class="bi bi-x"></i></button>`;
      html += `<span class="context-pill-label">${escapeHtml(item.name)}</span>`;
      html += `</span>`;
    }
    pillsContainer.innerHTML = html;

    // Remove handlers
    pillsContainer.querySelectorAll(".context-pill-remove").forEach(btn => {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        const pill = this.closest(".context-pill");
        removeItem(pill.dataset.type, pill.dataset.id);
        // Re-render picker if open to update toggle state
        if (!picker.classList.contains("d-none") && cachedItems) {
          renderResults(cachedItems);
        }
      });
    });

    if (typeof resizeOtherElements === "function") resizeOtherElements();
  }

  function syncHiddenField() {
    if (!hiddenField) return;
    if (selectedItems.length === 0) {
      hiddenField.value = "";
      return;
    }
    hiddenField.value = JSON.stringify(selectedItems.map(i => ({
      type: i.type,
      id: i.id,
      name: i.name,
    })));
  }

  // ========================================================================
  // Clear pills after form submission
  // ========================================================================

  if (promptForm) {
    promptForm.addEventListener("htmx:afterRequest", function (e) {
      if (e.detail.successful) {
        selectedItems = [];
        renderPills();
        syncHiddenField();
      }
    });
  }

  // ========================================================================
  // Keyboard navigation in picker
  // ========================================================================

  searchInput.addEventListener("keydown", function (e) {
    e.stopPropagation(); // Prevent chatInput.js from intercepting
    const items = resultsContainer.querySelectorAll(".context-picker-item");
    if (!items.length) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (items.length > 0) items[0].click();
    }
  });

  resultsContainer.addEventListener("keydown", function (e) {
    e.stopPropagation();
    if (!["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) return;
    e.preventDefault();

    const items = Array.from(resultsContainer.querySelectorAll(".context-picker-item"));
    const focused = document.activeElement;
    const idx = items.indexOf(focused);

    if (e.key === "ArrowDown" && idx < items.length - 1) {
      items[idx + 1].focus();
    } else if (e.key === "ArrowUp") {
      if (idx <= 0) {
        searchInput.focus();
      } else {
        items[idx - 1].focus();
      }
    } else if (e.key === "Enter" && idx >= 0) {
      items[idx].click();
    }
  });

  // ========================================================================
  // Helpers
  // ========================================================================

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function focusChatPrompt() {
    const chatPrompt = document.getElementById("chat-prompt");
    if (!chatPrompt) return;
    requestAnimationFrame(() => {
      try {
        chatPrompt.focus({preventScroll: true});
      } catch (err) {
        chatPrompt.focus();
      }
    });
  }

  // ========================================================================
  // @ shortcut: typing @ as the first character in an empty prompt opens the context picker
  // ========================================================================

  const chatPrompt = document.getElementById("chat-prompt");
  if (chatPrompt) {
    chatPrompt.addEventListener("keydown", function (e) {
      if (e.key === "@" && this.selectionStart === 0 && this.value === "") {
        e.preventDefault();
        openPicker({quickSelect: true});
      }
    });
  }

  // ========================================================================
  // Public API: load saved context hints
  // ========================================================================

  window.loadContextHints = function (hints) {
    // Clear existing selections
    selectedItems = [];
    // Add each hint as a selected item
    for (const hint of hints) {
      selectedItems.push({
        type: hint.type || "",
        id: hint.id || "",
        name: hint.name || "",
        description: hint.description || "",
        icon: hint.icon || "",
      });
    }
    renderPills();
    syncHiddenField();
  };

  /**
   * Add a context hint and close the specified modal.
   * Called externally by "Try it" (skills) and "Add to message" (librarian).
   * @param {Object} item - {type, id, name, description?, icon?}
   * @param {string} modalId - DOM id of the Bootstrap modal to close
   */
  window.addContextHintAndClose = function (item, modalId) {
    addItem({
      type: item.type || "",
      id: String(item.id || ""),
      name: item.name || "",
      description: item.description || "",
      icon: item.icon || "",
    });
    // Close the specified Bootstrap modal
    let needsDeferredFocus = false;
    if (modalId) {
      const modalEl = document.getElementById(modalId);
      if (modalEl) {
        const bsModal = bootstrap.Modal.getInstance(modalEl);
        if (bsModal) {
          needsDeferredFocus = true;
          let hasFocused = false;
          const focusAfterHide = function () {
            if (hasFocused) return;
            hasFocused = true;
            focusChatPrompt();
          };
          modalEl.addEventListener("hidden.bs.modal", focusAfterHide, {once: true});
          bsModal.hide();
          // Fallback in case hidden event isn't fired as expected.
          setTimeout(focusAfterHide, 400);
        }
      }
    }

    if (!needsDeferredFocus) {
      focusChatPrompt();
    }
  };
})();

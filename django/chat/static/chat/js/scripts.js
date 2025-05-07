const md = markdownit({
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre><code class="hljs">' +
          hljs.highlight(str, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (__) { }
    }

    return '<pre><code class="hljs">' + md.utils.escapeHtml(str) + '</code></pre>';
  },
  breaks: true,
});
md.use(katexPlugin);

const md_with_html = markdownit({
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre><code class="hljs">' +
          hljs.highlight(str, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (__) { }
    }

    return '<pre><code class="hljs">' + md.utils.escapeHtml(str) + '</code></pre>';
  },
  breaks: true,
  html: true,
});
md_with_html.use(katexPlugin);

function checkTruncation(element) {
  if (element && (element.offsetHeight < element.scrollHeight)) {
    element.closest('.message-outer').classList.add('truncate');
  }
}

function render_markdown(element) {
  // Render markdown in the element
  const markdown_text = element.querySelector(".markdown-text");
  dot_element = element.querySelector(".typing"); // Exists when dots=True on htmx_stream call
  if (markdown_text) {
    let to_parse = markdown_text.dataset.md;
    try {
      to_parse = JSON.parse(to_parse);
    } catch (e) {
      to_parse = false;
    }
    const parent = markdown_text.parentElement;
    if (to_parse) {
      parent.innerHTML = md.render(to_parse);
      const current_dots = parent.parentElement.querySelector(".typing");
      // If dots=True on htmx_stream call and we just removed the dots at the beginning of stream,
      // add a new dots element after parent
      if (dot_element && !current_dots) {
        parent.insertAdjacentHTML("afterend", "\n\n" + dot_element.outerHTML);
      }
      if (current_dots && !dot_element) {
        current_dots.remove();
      }
      // Add the "copy code" button to code blocks
      for (block of parent.querySelectorAll("pre code")) {
        block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
      }
    } else if ((after_text = parent.nextElementSibling)) {
      // If stream is empty (which should only happen between batches), it will stream dots
      // so we can remove the dot element we manually added above
      if (after_text.classList.contains("typing")) {
        after_text.remove();
      }
    }
  }
}

// Chat window UI
let autoscroll = true;
const scrollBtn = document.querySelector("#scroll-btn");

document.querySelector("#chat-container").addEventListener("scroll", function () {
  const threshold = 10; // pixels from the bottom considered "at the bottom"
  if ((this.scrollHeight - this.scrollTop - this.clientHeight) > threshold) {
    autoscroll = false;
    scrollBtn.classList.add("show");
  } else {
    autoscroll = true;
    scrollBtn.classList.remove("show");
  }
});

const copyCodeButtonHTML = `<button type="button" onclick="copyCode(this)"
class="btn btn-link m-0 p-0 text-muted copy-message-button copy-button"
title="Copy"><i class="bi bi-copy"></i><i class="bi bi-check-lg"></i></button>`;

function scrollToBottom(smooth = true, force = false) {
  resizePromptContainer();
  if (!autoscroll && !force) {
    return;
  }
  let messagesContainer = document.querySelector("#chat-container");
  let destination = messagesContainer.scrollHeight;
  // If there is currently a response streaming, disable smooth
  if (document.querySelector(".chat-streaming-response")) {
    smooth = false;
  }
  if (smooth) {
    messagesContainer.scrollTo({
      top: destination,
      behavior: "smooth"
    });
    return;
  }
  messagesContainer.scrollTop = destination;
}

function scrollToListItem() {
  setTimeout(() => {
    const currentChat = document.querySelector('.chat-list-item.current');
    if (currentChat) {
      currentChat.scrollIntoView({
        behavior: 'instant',
        block: 'center'
      });
    }
  }, 100);
}

function updatePlaceholder(mode) {
  // Update placeholder text
  const chat_prompt = document.querySelector("#chat-prompt");
  chat_prompt.placeholder = chat_prompt.dataset[`${mode}Placeholder`];
}

function handleModeChange(mode, element = null) {
  // Set the hidden input value to the selected mode
  let hidden_mode_input = document.querySelector('#id_mode');
  hidden_mode_input.value = mode;
  // Set the #chat-outer class to the selected mode for mode-specific styling
  document.querySelector('#chat-outer').classList = [mode];
  // Dispatch change event for search mode in order to trigger advance settings options
  document.getElementById('id_qa_mode').dispatchEvent(new Event("change"));
  updatePlaceholder(mode);
  resizeOtherElements();
  // If the invoking element is an accordion-button we can stop
  if (element && element.classList.contains("accordion-button")) return;
  // This setTimeout is necessary! Not sure why, but it won't expand the accordion without it.
  setTimeout(() => {updateAccordion(mode);}, 100);
}

function updateAccordion(mode) {
  // Otherwise, we need to update the accordion to show the correct mode
  let accordion_parent = document.querySelector("#options-accordion");
  for (chat_mode of ["chat", "summarize", "translate", "qa"]) {
    let accordion_button = accordion_parent.querySelector(`button[data-bs-target="#options-accordion-${chat_mode}"]`);
    let accordion_content = document.querySelector(`#options-accordion-${chat_mode}`);
    if (chat_mode === mode) {
      accordion_button.classList.remove("collapsed");
      accordion_button.attributes["aria-expanded"].value = "true";
      accordion_content.classList.add("show");
    } else {
      accordion_button.classList.add("collapsed");
      accordion_button.attributes["aria-expanded"].value = "false";
      accordion_content.classList.remove("show");
    }
  }
}

// Card links should change the mode dropdown and fire change event
function clickCard(mode) {
  toggleAriaSelected(mode);
  handleModeChange(mode);
  // Focus the chat input
  document.querySelector('#chat-prompt').focus();
}

function toggleAriaSelected(mode) {
  const cards = document.querySelectorAll('.nav-item button');
  cards.forEach(card => {
    card.classList.contains(`${mode}-option`) ? card.ariaSelected = true : card.ariaSelected = false;
  });
}


// Close the sidebars that are in "overlay mode" when clicking outside of them
document.querySelector("#chat-container").addEventListener('click', function (e) {
  let clicked_element = e.target;
  let left_sidebar = document.querySelector('#left-sidebar');
  let right_sidebar = document.querySelector('#right-sidebar');
  if (!(clicked_element.closest(".chat-sidebar-toggle") || left_sidebar.contains(clicked_element) || right_sidebar.contains(clicked_element))) {
    if (window.getComputedStyle(left_sidebar).position === "absolute" && !left_sidebar.classList.contains("hidden")) {
      closeSidebar("left-sidebar");
    }
    if (window.getComputedStyle(right_sidebar).position === "absolute" && !right_sidebar.classList.contains("hidden")) {
      closeSidebar("right-sidebar");
    }
  }
});

// Some resizing hacks to make the prompt form the same width as the messages
function resizePromptContainer() {
  let chatContainer = document.querySelector("#chat-container");
  let proposedWidth = (
    chatContainer.clientWidth - getComputedStyle(chatContainer).paddingLeft.replace("px", "")
  );
  document.querySelector('#prompt-form-container').style.width = proposedWidth + "px";
  resizeOtherElements();
}
function showHideSidebars() {
  if (window.innerWidth < 1390) {
    closeSidebar("right-sidebar", false);
  } else {
    openSidebar("right-sidebar", false);
  }
  if (window.innerWidth <= 1270) {
    closeSidebar("left-sidebar", false);
  } else {
    openSidebar("left-sidebar", false);
  }
  resizePromptContainer();
}
window.addEventListener('resize', showHideSidebars);
// On page load...
document.addEventListener("DOMContentLoaded", function () {
  // Markdown rendering
  document.querySelectorAll("div.message-text").forEach(function (element) {
    render_markdown(element);
    checkTruncation(element);
  });
  // The following line is causing problems. Keeping as a comment in case removing it causes other problems.
  // limitScopeSelect();
  showHideSidebars();
  document.querySelector('#prompt-form-container').classList.remove("d-none");
  resizeTextarea();
  let mode = document.querySelector('#chat-outer').classList[0];
  updateAccordion(mode);
  updateQaSourceForms();
  updatePlaceholder(mode);
  document.querySelector("#chat-prompt").focus();
  if (document.querySelector("#no-messages-placeholder") === null) {
    setTimeout(scrollToBottom, 100);
  }
  // Initialize tooltips
  const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl, {delay: {show: 500, hide: 200}}));
  const presetActionButtons = document.querySelectorAll("div.preset-actions button");
  presetActionButtons.forEach(function (tooltipTriggerEl) {
    tooltipTriggerEl.addEventListener('click', function () {
      tooltipList.forEach(function (tooltip) {
        tooltip.hide();
      });
    });
  });
  document.querySelectorAll('.chat-delete').forEach(button => {
    button.addEventListener('htmx:afterRequest', () => {
      deleteChatSection(button);
    });
  });

});
// On prompt form submit...
document.addEventListener("htmx:afterSwap", function (event) {
  if (event.detail?.target?.id != "messages-container") return;
  if (document.querySelector("#no-messages-placeholder") !== null) {
    document.querySelector("#no-messages-placeholder").remove();
  }
  // Check truncation
  document.querySelectorAll("div.message-text").forEach(function (element) {
    checkTruncation(element);
  });
  // Markdown rendering, if the response message has data-md property (e.g., error message)
  let messages = document.querySelectorAll("#messages-container div.markdown-text");
  let last_message = messages[messages.length - 1];
  if (last_message && last_message.dataset.md) {
    render_markdown(last_message.parentElement);
  }
  document.querySelector("#chat-prompt").value = "";
  if (!chat_tour_in_progress) {
    document.querySelector("#chat-prompt").focus();
  }
  // Change height back to minimum
  document.querySelector("#chat-prompt").style.height = chatPromptMinHeight + "px";
  lastHeight = chatPromptMinHeight;
  scrollToBottom(false, true);
});
// When streaming response is updated
document.addEventListener("htmx:sseMessage", function (event) {
  if (!(event.target.id?.startsWith("response-"))) return;
  render_markdown(event.target);
  scrollToBottom(false, false);
});
// When streaming response is finished
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id?.startsWith("message_"))) return;
  render_markdown(event.target);
  scrollToBottom(false, false);
});
// Title updated
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id === "current-chat-title")) return;
  updatePageTitle();
});
// When prompt input is focused, Enter sends message, unless Shift+Enter (newline)
document.addEventListener("keydown", function (event) {
  if (document.activeElement.id === "chat-prompt" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.querySelector("#send-button").click();
  }
});
// Accordion swapped
document.addEventListener("htmx:afterSwap", function (event) {
  if (event.detail?.target?.id !== "options-accordion") return;
  afterAccordionSwap();
});
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (event.detail?.target?.id !== "options-accordion") return;
  afterAccordionSwap();
});

// Sources modal setup
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id !== "sources-modal-inner") return;
  let targetElement = event.detail.target.querySelectorAll(".markdown-text");
  targetElement.forEach(function (element) {
    let decodedText = JSON.parse(element.dataset.md);
    let renderedMarkdown = md_with_html.render(decodedText);
    element.innerHTML = renderedMarkdown;
    element.querySelectorAll("a").forEach(function (link) {
      link.setAttribute("target", "_blank");
    });
  });
  // Hide #next-highlight if there are no "<mark>" elements
  if (event.detail.target.querySelector("mark") === null) {
    setTimeout(function () {
      // Check if the document.querySelector("#next-highlight") is visible
      if (document.querySelector("#next-highlight").classList.contains("d-none")) return;
      document.querySelector("#no-highlights").classList.remove("d-none");
      document.querySelector("#next-highlight").classList.add("d-none");
    }, 100);
  }
});

// reinitialize the delete button event listener after a chat is modified
document.addEventListener('htmx:afterRequest', function (event) {
  if (event.detail?.target?.id.startsWith('chat-list-item')) {
    const chat_id = event.detail.target.id.split("chat-list-item-")[1];
    const button = document.getElementById('delete-chat-' + chat_id);
    if (button) {
      button.addEventListener('htmx:afterRequest', () => {
        deleteChatSection(button);
      });
    }
  }
});

// deletes the list item associated with the deleted chat
// also checks if the section is now empty and removes it
function deleteChatSection(button) {
  // get chat id based on id of button
  var chat_id = button.id.split("delete-chat-")[1];
  // remove the chat list item associated with the deleted chat
  var chat_list_item = document.getElementById('chat-list-item-' + chat_id);
  chat_list_item.remove();

  // remove the section if it is now empty
  var section_number = button.getAttribute('data-section-number');
  var chat_list = document.getElementById('chat-list-' + section_number);
  if (chat_list.children.length === 0) {
    var section = document.getElementById('section-' + section_number);
    section.remove();
  }
}

// Message actions
function thumbMessage(clickedBtn) {
  isThumbDown = clickedBtn.classList.contains("thumb-down");
  isClicked = clickedBtn.classList.contains("clicked");
  clickedBtn.blur();
  clickedBtn.classList.toggle("clicked");
  let message = clickedBtn.closest(".message-outer");
  message.querySelectorAll(".thumb-message-button").forEach(function (btn) {
    if (clickedBtn !== btn) {
      btn.classList.remove("clicked");
    }
  });
  if (isThumbDown && !isClicked) {
    // Show the bootstrap modal (#modal)
    const feedbackModal = new bootstrap.Modal(
      document.getElementById('modal'),
      {"backdrop": true, "focus": true, "keyboard": true}
    );
    feedbackModal.show();
  }
}

/** Paste richly formatted text.
 * From https://stackoverflow.com/questions/23934656/how-can-i-copy-rich-text-contents-to-the-clipboard-with-javascript/77305170#77305170
 *
 * @param {string} rich - the text formatted as HTML
 * @param {string} plain - a plain text fallback
 */
async function pasteRich(rich, plain) {
  // Shiny new Clipboard API, not fully supported in Firefox.
  // https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API#browser_compatibility
  const html = new Blob([rich], {type: "text/html"});
  const text = new Blob([plain], {type: "text/plain"});
  const data = new ClipboardItem({"text/html": html, "text/plain": text});
  await navigator.clipboard.write([data]);
}

function copyMessage(btn) {
  let message = btn.closest(".message-outer");
  // Create a clone of the element in JS so we can remove the child "div.sources" if it exists
  const messageTextClone = message.querySelector(".message-text").cloneNode(true);
  const sources = messageTextClone.querySelector("div.sources");
  if (sources) {
    sources.remove();
  }
  let messageHtml = messageTextClone.outerHTML;
  let messageText = messageTextClone.innerText;
  // Remove whitespace
  //messageText = messageText.replace(/\s+/g, " ").trim();
  pasteRich(messageHtml, messageText);
  btn.blur();
  btn.classList.add("clicked");
  setTimeout(function () {
    btn.classList.remove("clicked");
  }, 2200);
}

function copyCode(btn) {
  let message = btn.closest("pre").querySelector("code");
  let codeText = message.innerText;
  navigator.clipboard.writeText(codeText);
  btn.blur();
  btn.classList.add("clicked");
  setTimeout(function () {
    btn.classList.remove("clicked");
  }, 300);
}

/** Copies the text from the user's prompt to the text input.
*
* @param {HTMLButtonElement} btn - the edit button of a user prompt.
* @param {string} messageMode - the current chat mode
*/
function copyPromptToTextInput(btn, messageMode) {
  let message = btn.closest(".message-outer");
  let messageText = message.querySelector(".message-text").innerText;

  const inputArea = document.getElementById("chat-prompt");

  inputArea.value = messageText;

  inputArea.dispatchEvent(new Event('change'));
  inputArea.focus();
}

function closeSidebar(sidebarID, resizePrompt = true) {
  document.querySelector("#" + sidebarID).classList.add("hidden");
  document.querySelector("#" + sidebarID + "-toggle").classList.remove("hidden");
  if (resizePrompt) {
    resizePromptContainer();
  }
}

function openSidebar(sidebarID, resizePrompt = true) {
  document.querySelector("#" + sidebarID).classList.remove("hidden");
  document.querySelector("#" + sidebarID + "-toggle").classList.add("hidden");
  if (resizePrompt) {
    resizePromptContainer();
  }
}

document.querySelector("#close-right-sidebar")
  .addEventListener("click", function () {closeSidebar("right-sidebar");});
document.querySelector("#right-sidebar-toggle")
  .addEventListener("click", function () {openSidebar("right-sidebar");});
document.querySelector("#close-left-sidebar")
  .addEventListener("click", function () {closeSidebar("left-sidebar");});
document.querySelector("#left-sidebar-toggle")
  .addEventListener("click", function () {openSidebar("left-sidebar");});

// Reload the page if navigated to with browser back / forward buttons
// This addresses a bug where the chat does not include all messages
// See https://stackoverflow.com/a/56851042
if (performance.getEntriesByType("navigation")[0].type === "back_forward") {
  location.reload();
}

function cancelChatRename() {
  document.querySelectorAll(".cancel-chat-rename-btn").forEach(function (btn) {
    btn.click();
  });
}

function updateQaModal() {
  // console.log('Updating QA modal');
  const qa_modal_elements = document.querySelectorAll('#advanced-qa-modal [data-inputname]');
  qa_modal_elements.forEach((modal_element) => {
    const hidden_input_name = modal_element.dataset.inputname;
    const hidden_field_element = document.querySelector(`input[name="${hidden_input_name}"]`);
    if (hidden_field_element) {
      if (modal_element.type === "checkbox") {
        modal_element.checked = hidden_field_element.value.toLowerCase() === "true";
        modal_element.value = modal_element.checked ? "true" : "false";
      } else {
        modal_element.value = hidden_field_element.value;
      }
    }
    toggleGranularOptions(document.getElementById('qa_answer_mode-modal').value);
  });
};
function updateQaHiddenField(modal_element) {
  // console.log('Updating QA hidden field');
  // Dataset attributes are lowercased
  const hidden_field_name = modal_element.dataset.inputname;
  const hidden_field_element = document.querySelector(`input[name="${hidden_field_name}"]`);
  if (hidden_field_element) {
    hidden_field_element.value = modal_element.value;
    hidden_field_element.dispatchEvent(new Event('change'));
  }
};

function toggleGranularOptions(value) {
  var gran_slider = document.getElementById('qa_granularity_slider');
  var pruning_toggle = document.getElementById('qa_pruning');

  if (value === 'per-source') {
    gran_slider.style.display = 'flex';
    pruning_toggle.style.display = '';
  } else {
    gran_slider.style.display = 'none';
    pruning_toggle.style.display = 'none';
  }
}


function toggleRagOptions(value) {
  var ragOptions = document.querySelectorAll('.qa_rag_option');

  ragOptions.forEach(function (option) {
    if (value !== 'rag') {
      option.style.display = 'none';
    } else {
      option.style.display = '';
    }
  });
}

// Hide RAG-only Q+A options in advanced modal on page load if applicable
document.addEventListener("DOMContentLoaded", function () {
  const ragOptions = document.querySelectorAll(".qa_rag_option");
  const mode = document.getElementById("id_qa_mode");
  ragOptions.forEach(function (option) {
    if (mode.value !== "rag") {
      option.style.display = "none";
    }
  });
});


function updatePageTitle(title = null) {
  if (title) {
    document.title = title;
    return;
  }
  const new_page_title = document.querySelector("#current-chat-title").dataset.pagetitle;
  if (new_page_title) document.title = new_page_title;
}

function emailChatAuthor(url) {
  htmx.ajax('GET', url, {target: '#author-mailto-container', swap: 'innerHTML'}).then(
    function () {
      document.querySelector("#author-mailto-container a").click();
      document.querySelector("#author-mailto-container").innerHTML = '';
    }
  );
}

function expandAllSources(message_id, force_expand = false) {
  const sources = document.querySelectorAll(`#sources-${message_id}-accordion .accordion-item`);
  const expandAllLabel = document.querySelector(`#expand-all-label`);
  const collapseAllLabel = document.querySelector(`#collapse-all-label`);
  const expandAll = collapseAllLabel.classList.contains("d-none") || force_expand;
  sources.forEach(function (source) {
    const accordion = new bootstrap.Collapse(source.querySelector('.accordion-collapse'), {toggle: false});
    expandAll ? accordion.show() : accordion.hide();
  });
  if (expandAll) {
    expandAllLabel.classList.add("d-none");
    collapseAllLabel.classList.remove("d-none");
  } else {
    expandAllLabel.classList.remove("d-none");
    collapseAllLabel.classList.add("d-none");
  }
}

function nextSourceHighlight(message_id) {
  const highlights = document.querySelectorAll(`#sources-${message_id}-accordion mark`);
  if (highlights.length === 0) return;
  const collapseAllLabel = document.querySelector(`#collapse-all-label`);
  const needToExpand = collapseAllLabel.classList.contains("d-none");
  if (needToExpand) expandAllSources(message_id, true);

  // Next highlight is either the next after the current one or the first one
  const currentHighlight = document.querySelector(`#sources-${message_id}-accordion mark.current-highlight`);
  let nextHighlight = highlights[0];
  if (currentHighlight) {
    currentHighlight.classList.remove("current-highlight");
    const nextIndex = Array.from(highlights).indexOf(currentHighlight) + 1;
    if (nextIndex < highlights.length) {
      nextHighlight = highlights[nextIndex];
    }
  }

  // Wait for the sources to expand before scrolling to the first highlight
  setTimeout(() => {
    nextHighlight.classList.add("current-highlight");
    nextHighlight.scrollIntoView({behavior: "smooth", block: "center"});
  }, needToExpand ? 300 : 0);
}

function clearRemainingCostWarningButtons() {
  const warningButton = document.querySelector(".cost-warning-buttons");
  if (warningButton) {
    warningButton.remove();
  }
}

function afterAccordionSwap() {
  const accordion = document.getElementById('options-accordion');
  const presetLoaded = accordion.dataset.presetLoaded === "true";
  const swap = accordion.dataset.swap === "true";
  const triggerLibraryChange = accordion.dataset.triggerLibraryChange === "true";
  const mode = accordion.dataset.mode;
  const prompt = accordion.dataset.prompt;
  if (prompt) {
    document.querySelector('#chat-prompt').value = prompt;
  }

  if (presetLoaded || swap) {
    handleModeChange(mode, null);
    const qa_mode_value = document.getElementById('id_qa_mode').value;
    switchToDocumentScope();
    // Update the advanced settings RAG options visibility
    toggleRagOptions(qa_mode_value);
    setTimeout(updateQaSourceForms, 100);
  } else if (triggerLibraryChange) {
    // This function calls updateQaSourceForms, so no need to call it twice
    resetQaAutocompletes();
  } else {
    updateQaSourceForms();
  }
}

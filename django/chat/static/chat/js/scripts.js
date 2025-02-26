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
let preventAutoScrolling = false;
let ignoreNextScrollEvent = true;

const copyCodeButtonHTML = `<button type="button" onclick="copyCode(this)"
class="btn btn-link m-0 p-0 text-muted copy-message-button copy-button"
title="Copy"><i class="bi bi-copy"></i><i class="bi bi-check-lg"></i></button>`;

function scrollToBottom(smooth = true, force = false) {
  resizePromptContainer();
  if (preventAutoScrolling && !force) {
    return;
  }

  ignoreNextScrollEvent = true;

  let messagesContainer = document.querySelector("#chat-container");
  let hashContainer = null;
  let hashRect = null;
  let containerRect = messagesContainer.getBoundingClientRect();

  if (window.location.hash) {
    hashContainer = document.querySelector(window.location.hash);
    hashRect = hashContainer.getBoundingClientRect();
  }

  const offset = (hashRect ? hashRect.top : 0) - containerRect.top;

  if (smooth) {
    messagesContainer.scrollTo({
      top: hashContainer ? messagesContainer.scrollTop + offset : messagesContainer.scrollHeight,
      behavior: "smooth",
    });
    return;
  }
  messagesContainer.scrollTop = hashContainer ? messagesContainer.scrollTop + offset : messagesContainer.scrollHeight;
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

function handleModeChange(mode, element = null, preset_loaded = false) {
  // Set the hidden input value to the selected mode
  let hidden_mode_input = document.querySelector('#id_mode');
  hidden_mode_input.value = mode;
  if (!preset_loaded) {triggerOptionSave();}
  // Set the #chat-outer class to the selected mode for mode-specific styling
  document.querySelector('#chat-outer').classList = [mode];

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

// When the user scrolls up, prevent auto-scrolling
let debounceTimer;
document.querySelector("#chat-container").addEventListener("scroll", function () {
  if (ignoreNextScrollEvent) {
    ignoreNextScrollEvent = false;
    return;
  }
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (this.scrollTop + this.clientHeight < this.scrollHeight - 5) {
      preventAutoScrolling = true;
    } else {
      preventAutoScrolling = false;
    }
  }, 10);
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
});
// On prompt form submit...
document.addEventListener("htmx:afterSwap", function (event) {
  if (event.detail?.target?.id != "messages-container") return;
  if (document.querySelector("#no-messages-placeholder") !== null) {
    document.querySelector("#no-messages-placeholder").remove();
  }
  if (event.detail.pathInfo.requestPath.includes('upload'))
    return;
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
  document.querySelector("#chat-prompt").focus();
  // Change height back to minimum
  document.querySelector("#chat-prompt").style.height = "85px";
  lastHeight = 85;
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

document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id === "sources-modal-inner") {
    var targetElement = event.detail.target.querySelectorAll(".markdown-text");
    targetElement.forEach(function (element) {
      var decodedText = JSON.parse(element.dataset.md);
      var renderedMarkdown = md_with_html.render(decodedText);
      element.innerHTML = renderedMarkdown;
      element.querySelectorAll("a").forEach(function (link) {
        link.setAttribute("target", "_blank");
      });
    });
  }
});

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


// File upload (based on https://github.com/shubhamkshatriya25/Django-AJAX-File-Uploader)
class FileUpload {

  constructor(input, upload_url, message_id) {
    this.input = input;
    this.upload_url = upload_url;
    this.message_id = message_id;
    this.progress_bar = document.querySelector(`#message_${message_id} .progress-bar`);
    this.cur_filename = document.querySelector(`#message_${message_id} .filename`);
    this.cur_filenum = document.querySelector(`#message_${message_id} .filenum`);
    this.total_filenum = document.querySelector(`#message_${message_id} .total-filenum`);
    this.progress_container = document.querySelector(`#message_${message_id} .progress-container`);
    this.max_chunk_size = 1024 * 512; // 512kb
  }

  upload() {
    this.total_filenum.innerHTML = this.input.files.length;
    this.cur_file_idx = 0;
    this.initFileUpload(this.cur_file_idx);
  }

  async sha256(buffer) {
    const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  }

  initFileUpload(i) {
    const file = this.input.files[i];
    this.file = file;
    this.cur_filename.innerHTML = file.name;
    this.cur_filenum.innerHTML = i + 1;
    this.progress_container.classList.remove("d-none");
    scrollToBottom(false);

    const reader = new FileReader();
    reader.onload = async (e) => {
      const buffer = e.target.result;
      const hash = await this.sha256(buffer);
      // console.log(`SHA-256 hash for ${file.name}: ${hash}`);
      this.upload_file(0, null, hash);
    };

    reader.readAsArrayBuffer(file);
  }

  upload_file(start, file_id, hash) {
    const formData = new FormData();
    const nextChunk = start + this.max_chunk_size + 1;
    const currentChunk = this.file.slice(start, nextChunk);
    const uploadedChunk = start + currentChunk.size;
    const end = (uploadedChunk >= this.file.size) ? 1 : 0;

    formData.append('file', currentChunk);
    formData.append('hash', hash);
    formData.append('filename', this.file.name);
    formData.append('end', end);
    formData.append('file_id', file_id);
    formData.append('nextSlice', nextChunk);
    formData.append('content_type', this.file.type);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', this.upload_url, true);
    xhr.setRequestHeader("X-CSRFToken", document.querySelector('[name=csrfmiddlewaretoken]').value);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const percent = (this.file.size < this.max_chunk_size)
          ? Math.round((e.loaded / e.total) * 100)
          : Math.round((uploadedChunk / this.file.size) * 100);
        this.progress_bar.style.width = percent + "%";
        this.progress_bar.parentElement.setAttribute("aria-valuenow", percent);
      }
    });

    xhr.onload = () => {
      if (xhr.status === 200) {
        const res = JSON.parse(xhr.responseText);
        if (res.data === "Invalid request") {
          alert(res.data);
        } else if (nextChunk < this.file.size && res.data !== "Uploaded successfully") {
          // upload file in chunks
          this.upload_file(nextChunk, res.file_id, hash);
        } else {
          // Upload finished. Upload the next file, if there is one
          this.cur_file_idx++;
          if (this.cur_file_idx < this.input.files.length) {
            // Replace the progress bar with a new one
            const new_progress = this.progress_bar.parentElement.cloneNode(true);
            new_progress.querySelector('.progress-bar').style.width = "0%";
            new_progress.setAttribute("aria-valuenow", 0);
            this.progress_bar.parentElement.replaceWith(new_progress);
            this.progress_bar = new_progress.querySelector('.progress-bar');
            this.initFileUpload(this.cur_file_idx);
          } else {
            // All files uploaded! Trigger the final response
            htmx.trigger(`#message_${this.message_id} .progress-container`, "done_upload");
          }
        }
      } else {
        alert(xhr.statusText);
      }
    };

    xhr.onerror = () => {
      alert(xhr.statusText);
    };

    xhr.send(formData);
  }
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
    // Dataset attributes are lowercased
    const hidden_input_name = modal_element.dataset.inputname;
    const hidden_field_element = document.querySelector(`input[name="${hidden_input_name}"]`);
    // console.log(hidden_input_name, hidden_field_element);
    if (hidden_field_element) {
      modal_element.value = hidden_field_element.value;
    }
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
  var gran = document.getElementById('qa_granularity-modal');

  var pruning_toggle = document.getElementById('qa_pruning');

  if (value === 'per-source') {
    gran_slider.style.display = 'flex';
    pruning_toggle.style.display = '';
  } else {
    gran_slider.style.display = 'none';
    gran.value = 768; // Reset slider value to 768 when "combined" is selected
    pruning_toggle.style.display = 'none';
  }

  updateQaHiddenField(gran);
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

function expandAllSources(message_id) {
  const sources = document.querySelectorAll(`#sources-${message_id}-accordion .accordion-item`);
  const expandAllLabel = document.querySelector(`#expand-all-label`);
  const collapseAllLabel = document.querySelector(`#collapse-all-label`);
  const expandAll = collapseAllLabel.classList.contains("d-none");
  sources.forEach(function (source) {
    const accordion = new bootstrap.Collapse(source.querySelector('.accordion-collapse'), {toggle: false});
    expandAll ? accordion.show() : accordion.hide();
  });
  expandAllLabel.classList.toggle("d-none");
  collapseAllLabel.classList.toggle("d-none");
}



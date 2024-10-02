// Chat window UI
let preventAutoScrolling = false;

const copyCodeButtonHTML = `<button type="button" onclick="copyCode(this)"
class="btn btn-link m-0 p-0 text-muted copy-message-button copy-button"
title="Copy"><i class="bi bi-clipboard"></i><i class="bi bi-clipboard-fill"></i></button>`;

function scrollToBottom(smooth = true, force = false) {
  resizePromptContainer();
  if (preventAutoScrolling && !force) {
    return;
  }
  let messagesContainer = document.querySelector("#chat-container");
  if (smooth) {
    messagesContainer.scrollTo({
      top: messagesContainer.scrollHeight,
      behavior: "smooth",
    });
    return;
  }
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function handleModeChange(mode, element = null) {
  // Set the hidden input value to the selected mode
  let hidden_mode_input = document.querySelector('#id_mode');
  hidden_mode_input.value = mode;
  triggerOptionSave();
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
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (this.scrollTop + this.clientHeight < this.scrollHeight - 1) {
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
  limitScopeSelect();
  showHideSidebars();
  document.querySelector('#prompt-form-container').classList.remove("d-none");
  resizeTextarea();
  let mode = document.querySelector('#chat-outer').classList[0];
  updateAccordion(mode);
  document.querySelector("#chat-prompt").focus();
  for (block of document.querySelectorAll("pre code")) {
    block.classList.add("language-txt");
    hljs.highlightElement(block);
    block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
  }
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
  if (event.target.id != "messages-container") return;
  if (document.querySelector("#no-messages-placeholder") !== null) {
    document.querySelector("#no-messages-placeholder").remove();
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
  if (!(event.target.id.startsWith("response-"))) return;
  for (block of event.target.querySelectorAll("pre code")) {
    block.classList.add("language-txt");
    hljs.highlightElement(block);
    block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
  }
  scrollToBottom(false, false);
});
// When streaming response is finished
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.target.id.startsWith("message_"))) return;
  for (block of event.target.querySelectorAll("pre code")) {
    block.classList.add("language-txt");
    hljs.highlightElement(block);
    block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
  }
  scrollToBottom(false, false);
});
// When prompt input is focused, Enter sends message, unless Shift+Enter (newline)
document.addEventListener("keydown", function (event) {
  if (document.activeElement.id === "chat-prompt" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.querySelector("#send-button").click();
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
  messageText = messageText.replace(/\s+/g, " ").trim();
  pasteRich(messageHtml, messageText);
  btn.blur();
  btn.classList.add("clicked");
  setTimeout(function () {
    btn.classList.remove("clicked");
  }, 300);
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

  initFileUpload(i) {
    var file = this.input.files[i];
    this.file = file;
    this.cur_filename.innerHTML = file.name;
    this.cur_filenum.innerHTML = i + 1;
    this.progress_container.classList.remove("d-none");
    scrollToBottom(false);
    this.upload_file(0, null);
  }

  upload_file(start, file_id) {
    var end;
    var self = this;
    var formData = new FormData();
    var nextChunk = start + this.max_chunk_size + 1;
    var currentChunk = this.file.slice(start, nextChunk);
    var uploadedChunk = start + currentChunk.size;
    if (uploadedChunk >= this.file.size) {
      end = 1;
    } else {
      end = 0;
    }
    formData.append('file', currentChunk);
    formData.append('filename', this.file.name);
    formData.append('end', end);
    formData.append('file_id', file_id);
    formData.append('nextSlice', nextChunk);
    formData.append('content_type', this.file.type);
    $.ajaxSetup({
      headers: {
        "X-CSRFToken": document.querySelector('[name=csrfmiddlewaretoken]').value,
      }
    });
    $.ajax({
      xhr: function () {
        var xhr = new XMLHttpRequest();
        xhr.upload.addEventListener('progress', function (e) {
          if (e.lengthComputable) {
            if (self.file.size < self.max_chunk_size) {
              var percent = Math.round((e.loaded / e.total) * 100);
            } else {
              var percent = Math.round((uploadedChunk / self.file.size) * 100);
            }
            self.progress_bar.style.width = percent + "%";
            self.progress_bar.parentElement.setAttribute("aria-valuenow", percent);
          }
        });
        return xhr;
      },

      url: this.upload_url,
      type: 'POST',
      dataType: 'json',
      cache: false,
      processData: false,
      contentType: false,
      data: formData,
      error: function (xhr) {
        alert(xhr.statusText);
      },
      success: function (res) {
        if (nextChunk < self.file.size) {
          // upload file in chunks
          file_id = res.file_id;
          self.upload_file(nextChunk, file_id);
        } else {
          // Upload finished. Upload the next file, if there is one
          self.cur_file_idx++;
          if (self.cur_file_idx < self.input.files.length) {
            // Replace the progress bar with a new one
            let new_progress = self.progress_bar.parentElement.cloneNode(true);
            new_progress.querySelector('.progress-bar').style.width = "0%";
            new_progress.setAttribute("aria-valuenow", 0);
            self.progress_bar.parentElement.replaceWith(new_progress);
            self.progress_bar = new_progress.querySelector('.progress-bar');
            self.initFileUpload(self.cur_file_idx);
          } else {
            // All files uploaded! Trigger the final response
            htmx.trigger(`#message_${self.message_id} .progress-container`, "done_upload");
          }
        }
      }
    });
  };
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
  console.log('Updating QA modal');
  const qa_modal_elements = document.querySelectorAll('#advanced-qa-modal [data-inputname]');
  qa_modal_elements.forEach((modal_element) => {
    // Dataset attributes are lowercased
    const hidden_input_name = modal_element.dataset.inputname;
    const hidden_field_element = document.querySelector(`input[name="${hidden_input_name}"]`);
    console.log(hidden_input_name, hidden_field_element);
    if (hidden_field_element) {
      modal_element.value = hidden_field_element.value;
    }
  });
};
function updateQaHiddenField(modal_element) {
  console.log('Updating QA hidden field');
  // Dataset attributes are lowercased
  const hidden_field_name = modal_element.dataset.inputname;
  const hidden_field_element = document.querySelector(`input[name="${hidden_field_name}"]`);
  if (hidden_field_element) {
    hidden_field_element.value = modal_element.value;
    hidden_field_element.dispatchEvent(new Event('change'));
  }
};

function toggleSlider(value) {
  var slider = document.getElementById('qa_granularity_slider');
  var sliderInput = document.getElementById('qa_granularity-modal');
  var numberInput = document.getElementById('qa_granularity_number-modal');
  // var granularityValue = document.getElementById('granularity_value');

  if (value === 'per-source') {
    slider.style.display = 'flex';
  } else {
    slider.style.display = 'none';
    sliderInput.value = 768; // Reset slider value to 768 when "combined" is selected
    numberInput.value = 768;
  }
}

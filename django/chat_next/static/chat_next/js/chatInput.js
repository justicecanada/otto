function getChatPromptMinHeight(textarea = document.querySelector('#chat-prompt')) {
  if (!textarea) {
    return 85;
  }

  const computedMinHeight = Number.parseFloat(window.getComputedStyle(textarea).minHeight);
  return Number.isFinite(computedMinHeight) ? computedMinHeight : 85;
}

const chatPromptMaxHeight = 400;

function calculateChatPromptHeight(textarea, {preserveLastHeight = true} = {}) {
  const chatPromptMinHeight = getChatPromptMinHeight(textarea);
  const baselineHeight = preserveLastHeight ? lastHeight : chatPromptMinHeight;

  return Math.min(
    Math.max(textarea.scrollHeight, baselineHeight, chatPromptMinHeight),
    chatPromptMaxHeight
  );
}

promptResizeHandle = document.getElementById("prompt-form-resize-handle");

// Resize the #chat-prompt textarea to fit its content, up to a maximum height of 400px
let lastHeight = document.querySelector('#chat-prompt').clientHeight;
function resizeTextarea() {
  let textarea = document.querySelector('#chat-prompt');
  // Reset the height to its default to get the correct scrollHeight
  textarea.style.height = 'auto';
  // Calculate the new height and limit it to 400px
  let newHeight = calculateChatPromptHeight(textarea);
  lastHeight = newHeight;
  textarea.style.height = newHeight + 'px';
  resizeOtherElements();
}

function syncTextareaHeightToViewport() {
  const textarea = document.querySelector('#chat-prompt');
  if (!textarea) return;

  textarea.style.height = 'auto';
  const newHeight = calculateChatPromptHeight(textarea, {preserveLastHeight: false});

  textarea.style.height = newHeight + 'px';
  lastHeight = newHeight;
  resizeOtherElements();
}

// Helper to move the caret of #chat-prompt to the end
function moveChatPromptCaretToEnd() {
  const textarea = document.getElementById('chat-prompt');
  if (!textarea) return;
  const len = textarea.value.length;
  textarea.focus();
  if (typeof textarea.setSelectionRange === 'function') {
    textarea.setSelectionRange(len, len);
  } else if (textarea.createTextRange) { // IE fallback
    const range = textarea.createTextRange();
    range.collapse(false);
    range.select();
  }
}

function updateToolbarDensity() {
  const chatToolbar = document.querySelector('#chat-toolbar');
  const hideables = document.querySelectorAll('#chat-toolbar .hideable');

  if (!chatToolbar || !hideables.length) return;

  hideables.forEach((element) => element.classList.remove('d-none'));

  if (chatToolbar.clientHeight > 50) {
    hideables.forEach((element) => element.classList.add('d-none'));
  }
}

function resizeOtherElements() {
  updateToolbarDensity();

  const chatInputHeight = document.querySelector('#prompt-form-container').clientHeight;
  const chatContainer = document.querySelector('#chat-container');
  if (!chatContainer) return;

  chatContainer.style.paddingBottom = `${chatInputHeight}px`;
}

// Add the input event listener to the textarea
document.querySelector('#chat-prompt').addEventListener('input', resizeTextarea);

// Resize the #chat-prompt textarea when the #prompt-form-resize-handle is dragged
let isResizing = false;
let lastDownY = 0;
let originalHeight = document.querySelector('#chat-prompt').clientHeight;
promptResizeHandle.addEventListener('mousedown', function (e) {
  isResizing = true;
  lastDownY = e.clientY;
  originalHeight = document.querySelector('#chat-prompt').clientHeight;

  function mouseMoveHandler(e) {
    if (!isResizing) return;
    let textarea = document.querySelector('#chat-prompt');
    const chatPromptMinHeight = getChatPromptMinHeight(textarea);
    let newHeight = Math.max(Math.min(originalHeight + lastDownY - e.clientY, chatPromptMaxHeight), chatPromptMinHeight);
    textarea.style.height = newHeight + 'px';
    resizeOtherElements(newHeight);
  }

  function mouseUpHandler() {
    if (isResizing) {
      isResizing = false;
      lastHeight = document.querySelector('#chat-prompt').clientHeight + 1;
      document.querySelector('#chat-prompt').focus();
    }
    document.removeEventListener('mousemove', mouseMoveHandler);
    document.removeEventListener('mouseup', mouseUpHandler);
  }

  document.addEventListener('mousemove', mouseMoveHandler);
  document.addEventListener('mouseup', mouseUpHandler);
});

window.addEventListener('resize', function () {
  syncTextareaHeightToViewport();
});

// Prompt generator (Improve prompt) is intentionally removed from chat_next.

resizeOtherElements();

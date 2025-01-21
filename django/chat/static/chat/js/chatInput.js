const chatPromptMinHeight = 85;
const chatPromptMaxHeight = 400;

promptResizeHandle = document.getElementById("prompt-form-resize-handle");

// Resize the #chat-prompt textarea to fit its content, up to a maximum height of 400px
let lastHeight = document.querySelector('#chat-prompt').clientHeight;
function resizeTextarea() {
  let textarea = document.querySelector('#chat-prompt');
  // Reset the height to its default to get the correct scrollHeight
  textarea.style.height = 'auto';
  // Calculate the new height and limit it to 400px
  let newHeight = Math.min(Math.max(textarea.scrollHeight + 1, lastHeight), chatPromptMaxHeight);
  lastHeight = newHeight;
  textarea.style.height = newHeight + 'px';
  resizeOtherElements();
}

function resizeOtherElements() {
  // If the #chat-toolbar has split into two rows, it will be > 50px high. Try to make it smaller
  const hideables = document.querySelectorAll('#chat-toolbar .hideable');
  hideables.forEach(el => el.classList.remove('d-none'));
  const chatToolbarHeight = document.querySelector('#chat-toolbar').clientHeight;
  if (chatToolbarHeight > 50) {
    hideables.forEach(el => el.classList.add('d-none'));
  }
  const chatInputHeight = document.querySelector('#prompt-form-container').clientHeight;
  const chatContainer = document.querySelector('#chat-container');
  // Check if the chatContainer is scrolled to bottom
  let isScrolledToBottom = chatContainer.scrollHeight - chatContainer.clientHeight <= chatContainer.scrollTop + 1;
  chatContainer.style.paddingBottom = `${chatInputHeight}px`;
  if (isScrolledToBottom) chatContainer.scrollTop = chatContainer.scrollHeight;
}

function handleChatPromptResize(event) {
  let textarea = document.querySelector('#chat-prompt');
  const originalHeight = textarea.clientHeight;
  switch (event.key) {
    case "ArrowUp":
      newHeight = Math.min(originalHeight + 10, chatPromptMaxHeight);
      textarea.style.height = newHeight + "px";
      resizeOtherElements(newHeight);;
      event.preventDefault();
      break;
    case "ArrowDown":
      newHeight = Math.max(originalHeight - 10, chatPromptMinHeight);
      textarea.style.height = newHeight + "px";
      event.preventDefault();
      resizeOtherElements(newHeight);;
      break;
    default:
      break;
  }
}

// Add the input event listener to the textarea
document.querySelector('#chat-prompt').addEventListener('input', resizeTextarea);
// Add the keydown event listener to the prompt resize handle
promptResizeHandle.addEventListener("keydown", handleChatPromptResize);

// Resize the #chat-prompt textarea when the #prompt-form-resize-handle is dragged
let isResizing = false;
let lastDownY = 0;
let originalHeight = document.querySelector('#chat-prompt').clientHeight;
promptResizeHandle.addEventListener('mousedown', function (e) {
  isResizing = true;
  lastDownY = e.clientY;
  originalHeight = document.querySelector('#chat-prompt').clientHeight;
});
document.addEventListener('mousemove', function (e) {
  if (!isResizing) return;
  let textarea = document.querySelector('#chat-prompt');
  let newHeight = Math.max(Math.min(originalHeight + lastDownY - e.clientY, chatPromptMaxHeight), chatPromptMinHeight);
  textarea.style.height = newHeight + 'px';
  resizeOtherElements(newHeight);
});
document.addEventListener('mouseup', function () {
  if (isResizing) {
    isResizing = false;
    lastHeight = document.querySelector('#chat-prompt').clientHeight + 1;
    document.querySelector('#chat-prompt').focus();
  }
});



document.getElementById('magic-form').addEventListener('submit', function (event) {
  event.preventDefault();  // Prevent the default form submission

  const userInput = document.getElementById('magic-prompt').value;
  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

  fetch('/chat/generate-prompt/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': csrfToken
    },
    body: new URLSearchParams({
      'user_input': userInput
    })
  })
    .then(response => response.json())
    .then(data => {
      if (data.output_text) {
        document.getElementById('generated-prompt').value = data.output_text;
      } else {
        console.error('Error:', data.error);
      }
    })
    .catch(error => {
      console.error('Error:', error);
    });
});


function toggleSpinner(show) {
  const magicIcon = document.getElementById('generate-text');
  const spinnerIcon = document.getElementById('spinner-icon');
  if (show) {
    magicIcon.style.display = 'none';
    spinnerIcon.style.display = 'inline-block';
  } else {
    magicIcon.style.display = 'inline-block';
    spinnerIcon.style.display = 'none';
  }
}

// document.getElementById('generate-prompt').addEventListener('click', function () {
//   toggleSpinner(true);
// });

document.addEventListener('htmx:afterRequest', function () {
  toggleSpinner(false);
});

function applyGeneratedPrompt(event) {
  event.preventDefault();
  const generatedPrompt = document.getElementById('generated-prompt').value;
  const chatPrompt = document.getElementById('chat-prompt');
  chatPrompt.value = generatedPrompt;
  // Close the modal
  const modalElement = document.getElementById('magicModal');
  const modal = bootstrap.Modal.getInstance(modalElement);
  modal.hide();

}
function clearTextAreas() {

  document.getElementById('generated-prompt').value = '';
}
function setMagicPrompt() {
  const chatPrompt = document.getElementById('chat-prompt').value;
  document.getElementById('magic-prompt').value = chatPrompt;
}

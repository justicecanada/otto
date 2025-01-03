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
// // Add the "copy code" button to code blocks NOT WORKING
// const copyCodeButtonHTML = '<button class="copy-code-button">Copy</button>';

// for (const block of document.querySelectorAll("pre code")) {
//   block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
// }

// // Add event listener for copy buttons in code blocks  NOT WORKING
// document.addEventListener('click', function (event) {
//   if (event.target.classList.contains('copy-code-button')) {
//     const codeBlock = event.target.nextElementSibling;
//     if (codeBlock && codeBlock.tagName === 'CODE') {
//       navigator.clipboard.writeText(codeBlock.textContent).then(function () {
//         alert('Copied to clipboard!');
//       }).catch(function (err) {
//         console.error('Could not copy text: ', err);
//       });
//     }
//   }
// });

// Add event listener for the "Generate" button
const generatePromptButton = document.getElementById('generate-prompt');
if (generatePromptButton) {
  generatePromptButton.addEventListener('click', function () {
    const userMessage = document.getElementById('chat-prompt').value;
    const magicPrompt = document.getElementById('magic-prompt');
    if (userMessage) {
      // Send the userMessage to the AI prompt generator endpoint
      fetch('/api/generate-prompt', { //use anthropic's API key
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({message: userMessage}),
      })
        .then(response => response.json())
        .then(data => {
          // Display the generated prompt in the magicPrompt textarea
          magicPrompt.value = data.generated_prompt;
        })
        .catch(error => {
          console.error('Error generating prompt:', error);
        });
    }
  });
};

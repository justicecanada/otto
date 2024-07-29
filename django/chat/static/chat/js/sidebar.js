let optionSaveTimeout = null;
let optionSaveDelay = 500;

const handlebar = document.getElementById("right-sidebar-resize-handle");
const sidebar = document.getElementById("right-sidebar");

const sidebarMinWidth = 350;
const sidebarMaxWidth = 600;

// This saves the options when the user changes them via HTMX
function triggerOptionSave() {
  document.querySelector('#chat-options').dispatchEvent(new Event('optionsChanged'));
}

function optionPresetDropdown() {
  let el = document.querySelector('#option_presets input');
  el.value = '';
  el.focus();
}

function handleRightSidebarResize(event) {
  const originalWidth = sidebar.clientWidth;
  switch (event.key) {
    case "ArrowLeft":
      newWidth = Math.min(originalWidth + 10, sidebarMaxWidth);
      sidebar.style.width = newWidth + "px";
      sidebar.style.minWidth = newWidth + "px";
      resizePromptContainer();
      event.preventDefault();
      break;
    case "ArrowRight":
      newWidth = Math.max(originalWidth - 10, sidebarMinWidth);
      sidebar.style.width = newWidth + "px";
      sidebar.style.minWidth = newWidth + "px";
      event.preventDefault();
      resizePromptContainer();
      break;
    default:
      break;
  }
}

handlebar.addEventListener("keydown", handleRightSidebarResize);

// Resize the #right-sidebar when the #right-sidebar-resize-handle is dragged
(function () {
  let isResizing = false;
  let lastDownX = 0;
  let originalWidth = document.querySelector('#right-sidebar').clientWidth;
  document.querySelector('#right-sidebar-resize-handle').addEventListener('mousedown', function (e) {
    isResizing = true;
    lastDownX = e.clientX;
    originalWidth = document.querySelector('#right-sidebar').clientWidth;
  });
  document.addEventListener('mousemove', function (e) {
    if (!isResizing) return;
    let sidebar = document.querySelector('#right-sidebar');
    let newWidth = Math.max(Math.min(originalWidth + lastDownX - e.clientX, sidebarMaxWidth), sidebarMinWidth);
    sidebar.style.width = newWidth + 'px';
    sidebar.style.minWidth = newWidth + 'px';
    resizePromptContainer();
  });
  document.addEventListener('mouseup', function () {
    if (isResizing) {
      isResizing = false;
    }
  });
})();

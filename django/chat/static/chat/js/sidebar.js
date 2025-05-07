let optionSaveTimeout = null;
let optionSaveDelay = 500;

const handlebar = document.getElementById("right-sidebar-resize-handle");
const sidebar = document.getElementById("right-sidebar");

// Check if html has lang="fr" attribute
const lang_fr = document.documentElement.lang === "fr";
const sidebarMinWidth = lang_fr ? 400 : 350;
const sidebarMaxWidth = 600;

// This saves the options when the user changes them via HTMX
function triggerOptionSave() {
  // Re-enable any disabled inputs within #chat-options form
  let previously_disabled_elements = document.querySelectorAll('#chat-options select:disabled');
  document.querySelectorAll('#chat-options select').forEach((el) => {
    el.removeAttribute('disabled');
  });
  document.querySelector('#chat-options').dispatchEvent(new Event('optionsChanged'));
  // Disable the inputs that were previously disabled
  previously_disabled_elements.forEach((el) => {
    el.setAttribute('disabled', 'disabled');
  });
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

    function onMouseMove(e) {
      if (!isResizing) return;
      let sidebar = document.querySelector('#right-sidebar');
      let newWidth = Math.max(Math.min(originalWidth + lastDownX - e.clientX, sidebarMaxWidth), sidebarMinWidth);
      sidebar.style.width = newWidth + 'px';
      sidebar.style.minWidth = newWidth + 'px';
      resizePromptContainer();
    }

    function onMouseUp() {
      if (isResizing) {
        isResizing = false;
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
      }
    }

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
})();

function onOptionsAccordionSwap(e, preset_loaded, swap, prompt, mode, triggerLibraryChange) {
  if (!e.target || e.target.id !== "options-accordion") return;
  console.log("Options accordion swap event triggered");
  if (prompt) {
    document.querySelector('#chat-prompt').value = prompt;
  }
  if (preset_loaded || swap) {
    console.log("Preset loaded or swap triggered");
    handleModeChange(mode, null, preset_loaded);
    const qa_mode_value = document.getElementById('id_qa_mode').value;
    switchToDocumentScope();
    // Update the advanced settings RAG options visibility
    toggleRagOptions(qa_mode_value);
    setTimeout(updateQaSourceForms, 100);
  } else if (triggerLibraryChange) {
    console.log("Trigger library change");
    // This function calls updateQaSourceForms, so no need to call it twice
    resetQaAutocompletes();
  } else {
    console.log("No preset loaded or swap triggered");
    updateQaSourceForms();
  }
  console.log("Updating autocomplete library IDs");
  updateLibraryModalButton();
  updateAutocompleteLibraryid('id_qa_data_sources__textinput');
  updateAutocompleteLibraryid('id_qa_documents__textinput');

  document.body.removeEventListener('htmx:oobAfterSwap', onOptionsAccordionSwap);
}

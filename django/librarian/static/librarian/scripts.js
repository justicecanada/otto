function toggleWarning(public_checkbox) {
  if (public_checkbox.checked) {
    document.getElementById("public-library-warning").classList.remove("d-none");
  } else {
    document.getElementById("public-library-warning").classList.add("d-none");
  }
}

let librarianModalCloseHandler = event => {
  // Stop polling on element id="libraryModalPoller"
  // by replacing it with empty div
  const poller = document.getElementById('libraryModalPoller');
  const newPoller = document.createElement('div');
  newPoller.id = 'libraryModalPoller';
  poller.parentNode.replaceChild(newPoller, poller);
  // Fake library ID which won't exist. If passed through, this will reset the QA library to the default.
  let library_id = 99999999999999999999;
  const selected_library_li = document.querySelector("#librarian-libraries li.list-group-item[aria-selected='true']");
  if (selected_library_li) {
    library_id = selected_library_li.getAttribute('data-library-id');
  }
  // Update the QA library select
  htmx.ajax('GET', `/chat/id/${chat_id}/options/set_qa_library/${library_id}`, {target: '#options-accordion'}).then(() => {
    // Reset QA autocompletes on edit library modal close
    resetQaAutocompletes();
    triggerOptionSave();
  });
};
const modalEl = document.getElementById('editLibrariesModal');
modalEl.addEventListener('hidden.bs.modal', librarianModalCloseHandler);

const MAX_FILES_PER_DATA_SOURCE = 100;
const MAX_SIZE_MB = 300;
// Translations are set in document_list_script.html
let files_max_string_start = `You can only upload a maximum of`;
let files_max_string_end = `files per folder.`;
let files_remaining_string_end = `files remaining.`;
let max_file_size_string_start = `You can only upload a total file size of`;
let max_file_size_string_end = `at one time.`;

function validateAndUpload() {
  document.querySelectorAll('#librarian-documents li.temp').forEach((el) => {el.remove();});
  let current_file_count = document.querySelectorAll('#librarian-documents li.list-group-item').length;
  const fileInput = document.querySelector('#document-file-input');
  fileInput.value = null;
  fileInput.click();

  fileInput.onchange = function () {
    const files = fileInput.files;
    let files_remaining = MAX_FILES_PER_DATA_SOURCE - current_file_count;
    if (files.length > files_remaining) {
      alert(`${files_max_string_start} ${MAX_FILES_PER_DATA_SOURCE} ${files_max_string_end} (${files_remaining} ${files_remaining_string_end})`);
      fileInput.value = null;
      return;
    }

    let totalSize = 0;
    for (let i = 0; i < files.length; i++) {
      totalSize += files[i].size;
    }

    if (totalSize > MAX_SIZE_MB * 1024 * 1024) {
      alert(`${max_file_size_string_start} ${MAX_SIZE_MB} MB ${max_file_size_string_end}`);
      fileInput.value = null;
      return;
    }

    document.querySelector('#document-upload-form').dispatchEvent(new Event('startUpload'));
  };
}

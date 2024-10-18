function toggleWarning(public_checkbox) {
  if (public_checkbox.checked) {
    document.getElementById("public-library-warning").classList.remove("d-none");
  } else {
    document.getElementById("public-library-warning").classList.add("d-none");
  }
}

let librarianModalCloseHandler = null;

updateLibrarianModalOnClose = (library_id) => {
  const modalEl = document.getElementById('editLibrariesModal');
  modalEl.removeEventListener('hidden.bs.modal', librarianModalCloseHandler);
  librarianModalCloseHandler = event => {
    // Stop polling on element id="libraryModalPoller"
    // by replacing it with empty div
    const poller = document.getElementById('libraryModalPoller');
    const newPoller = document.createElement('div');
    newPoller.id = 'libraryModalPoller';
    poller.parentNode.replaceChild(newPoller, poller);
    // Update the QA library select
    const qa_library_select = document.getElementById("id_qa_library");
    // Only update if the value is different (allow type coercion)
    if (qa_library_select.value != library_id) {
      qa_library_select.value = library_id;
      qa_library_select.dispatchEvent(new Event('change'));
    }
  };
  modalEl.addEventListener('hidden.bs.modal', librarianModalCloseHandler);
};

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
  const chatId = modalEl.getAttribute('data-chat-id');
  modalEl.removeEventListener('hidden.bs.modal', librarianModalCloseHandler);
  librarianModalCloseHandler = event => {
    const qa_library_select = document.getElementById("id_qa_library");
    if (qa_library_select.value !== library_id) {
      qa_library_select.value = library_id;
      qa_library_select.dispatchEvent(new Event('change'));
    }
  };
  modalEl.addEventListener('hidden.bs.modal', librarianModalCloseHandler);
};

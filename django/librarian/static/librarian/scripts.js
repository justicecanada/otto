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
    htmx.ajax('GET', `/chat/id/${chatId}/options/qa_accordion/${library_id}`, {swap: 'none'});
  };
  modalEl.addEventListener('hidden.bs.modal', librarianModalCloseHandler);
};

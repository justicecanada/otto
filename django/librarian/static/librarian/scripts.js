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
  var prev_library_id = document.getElementById('id_qa_library').value;
  const selected_library_li = document.querySelector("#librarian-libraries li.list-group-item[aria-selected='true']");
  if (selected_library_li) {
    library_id = selected_library_li.getAttribute('data-library-id');
  }
  // Update the QA library select
  htmx.ajax('GET', `/chat/id/${chat_id}/options/set_qa_library/${library_id}`, {target: '#options-accordion', swap: 'outerHTML'}).then(() => {
    updateAccordion('qa');
    triggerOptionSave();
  });
};
const modalEl = document.getElementById('editLibrariesModal');
modalEl.addEventListener('hidden.bs.modal', librarianModalCloseHandler);

function emailLibraryAdmins(url) {
  const library_id = document.getElementById("id_qa_library").value;
  url = url.replace('0', library_id);
  htmx.ajax('GET', url, {target: '#email_library_admins_link', swap: 'innerHTML'}).then(
    function () {
      document.querySelector("#email_library_admins_link a").click();
      document.querySelector("#email_library_admins_link").innerHTML = '';
    }
  );
}

function initLibrarianUploadForm() {
  const metadata = {};
  const upload_message = document.getElementById("librarian-upload-message");
  const details_container = document.getElementById("librarian-details");
  const file_input = document.getElementById("id_librarian-input_file");
  function submitUploadsIfComplete() {
    const form = document.getElementById("librarian-upload-form");
    const allDone = Array.from(form.querySelectorAll(".dff-file")).every(file => {
      return file.classList.contains("dff-upload-success") || file.classList.contains("dff-upload-fail");
    });
    if (allDone) {
      // Use htmx directly instead of native form submission
      htmx.trigger(form, 'htmx:beforeRequest');
      htmx.ajax('POST', form.getAttribute('hx-post'), {
        source: form,
        target: form.getAttribute('hx-target'),
        swap: form.getAttribute('hx-swap'),
      });
    }
    hideIfNoFiles();
  }
  function hideIfNoFiles() {
    const form = document.getElementById("librarian-upload-form");
    const numFiles = form.querySelectorAll(".dff-file").length;
    if (numFiles === 0) {
      upload_message.classList.add("d-none");
      details_container.classList.remove("d-none");
    } else {
      upload_message.classList.remove("d-none");
      details_container.classList.add("d-none");
    }
  }
  initUploadFields(document.getElementById("librarian-upload-form"), {
    prefix: "librarian",
    supportDropArea: false,
    callbacks: {
      onSuccess: (upload) => {
        const filename = upload.name;
        const contentType = upload.upload.file.type;
        metadata[filename] = {type: contentType};
        const metadataField = document.querySelector("#id_librarian-input_file-metadata");
        if (metadataField) {
          metadataField.value = JSON.stringify(metadata);
        }
        submitUploadsIfComplete();
      },
      onError: (upload) => submitUploadsIfComplete(),
      onDelete: (upload) => submitUploadsIfComplete(),
      onProgress: (bytesUploaded, bytesTotal, upload) => {
        if (bytesTotal > LIBRARIAN_MAX_UPLOAD_SIZE) {
          // Find the .dff-file which contains span.dff-filename with text `upload.name`;
          const fileElements = document.querySelectorAll('#librarian-upload-form .dff-file');
          const fileElement = Array.from(fileElements).find((fileElement) => {
            const filenameElement = fileElement.querySelector('.dff-filename');
            return filenameElement && filenameElement.innerHTML === upload.name;
          });
          if (fileElement) {
            // Remove cancel link and progress bar
            const cancel_link = fileElement.querySelector('a.dff-cancel');
            const progress_bar = fileElement.querySelector('.dff-progress');
            const error_message = fileElement.querySelector('.dff-error');
            if (cancel_link) {
              cancel_link.remove();
            }
            if (progress_bar) {
              progress_bar.remove();
            }
            if (!error_message) {
              const error_message = document.createElement('span');
              error_message.className = 'dff-error';
              error_message.innerText = LIBRARIAN_UPLOAD_TOO_LARGE;
              fileElement.appendChild(error_message);
            }
          }
          // Send "terminate" signal to delete the partial upload
          upload.abort(true);
          // Wait 2 seconds before adding class dff-upload-fail to the dff-file
          // to allow the error message to be displayed
          setTimeout(() => {
            fileElement.classList.add("dff-upload-fail");
            submitUploadsIfComplete();
          }, 2000);
        }
      },
    }
  });
  file_input.addEventListener("change", function () {
    // Show the upload container
    upload_message.classList.remove("d-none");
    details_container.classList.add("d-none");
  });
}

document.addEventListener('htmx:afterSwap', function (event) {
  if (event.target.id === "librarian-upload-message") {
    initLibrarianUploadForm();
  }
});

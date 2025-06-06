// Template wizard scripts
function setActiveTab(e) {
  const tabs = document.querySelectorAll('#top-tabs .nav-link');
  tabs.forEach(tab => {
    tab.classList.remove('active');
    tab.classList.remove('fw-semibold');
  });
  e.classList.add('active');
  e.classList.add('fw-semibold');
}

const cards = document.querySelectorAll('#preset-card-list li.card');
cards.forEach((card) => {
  // When card is clicked (except for buttons and links),
  // trigger click event on child a.preset-load-link
  card.addEventListener('click', (event) => {
    const target = event.target;
    // Ensure target is not a button or link, or WITHIN a button or link
    if (target.tagName !== 'A' && target.tagName !== 'BUTTON' && !target.closest('a') && !target.closest('button')) {
      const link = card.querySelector('a.preset-load-link');
      if (link) {
        link.click();
      }
    }
  });
});

function initTemplateWizardUploadForm() {
  const metadata = {};
  const upload_message = document.getElementById("template-wizard-upload-message");
  const file_input = document.getElementById("id_template-wizard-input_file");
  function submitUploadsIfComplete() {
    const form = document.getElementById("template-wizard-upload-form");
    const allDone = Array.from(form.querySelectorAll(".dff-file")).every(file => {
      return file.classList.contains("dff-upload-success") || file.classList.contains("dff-upload-fail");
    });
    if (allDone) {
      document.querySelector("#upload-spinner").classList.remove("d-none");
      form.submit();
    }
    hideIfNoFiles();
  }
  function hideIfNoFiles() {
    const form = document.getElementById("template-wizard-upload-form");
    const numFiles = form.querySelectorAll(".dff-file").length;
    if (numFiles === 0) {
      upload_message.classList.add("d-none");
    } else {
      upload_message.classList.remove("d-none");
    }
  }
  if (file_input) {
    initUploadFields(document.getElementById("template-wizard-upload-form"), {
      prefix: "template-wizard",
      supportDropArea: false,
      callbacks: {
        onSuccess: (upload) => {
          const filename = upload.name;
          const contentType = upload.upload.file.type;
          metadata[filename] = {type: contentType};
          const metadataField = document.querySelector("#id_template-wizard-input_file-metadata");
          if (metadataField) {
            metadataField.value = JSON.stringify(metadata);
          }
          submitUploadsIfComplete();
        },
        onError: (upload) => submitUploadsIfComplete(),
        onDelete: (upload) => submitUploadsIfComplete(),
        onProgress: (bytesUploaded, bytesTotal, upload) => {
          if (typeof TEMPLATE_WIZARD_MAX_UPLOAD_SIZE !== 'undefined' && bytesTotal > TEMPLATE_WIZARD_MAX_UPLOAD_SIZE) {
            const fileElements = document.querySelectorAll('#template-wizard-upload-form .dff-file');
            const fileElement = Array.from(fileElements).find((fileElement) => {
              const filenameElement = fileElement.querySelector('.dff-filename');
              return filenameElement && filenameElement.innerHTML === upload.name;
            });
            if (fileElement) {
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
                error_message.innerText = TEMPLATE_WIZARD_UPLOAD_TOO_LARGE;
                fileElement.appendChild(error_message);
              }
            }
            upload.abort(true);
            setTimeout(() => {
              fileElement.classList.add("dff-upload-fail");
              submitUploadsIfComplete();
            }, 2000);
          }
        },
      }
    });
    file_input.addEventListener("change", function () {
      upload_message.classList.remove("d-none");
    });
  }
}

(function () {
  initTemplateWizardUploadForm();

  const upload_button = document.getElementById("upload-button");
  if (upload_button) {
    upload_button.addEventListener("click", function () {
      document.querySelector('#id_template-wizard-input_file').click();
    });
  }
})();

function handleDeleteSession(event, el) {
  // Only prevent dropdown closing if there is more than 1 session item
  const li = el.closest('li');
  const ul = li && li.parentElement;
  if (ul) {
    // Count only visible session items (not the 'No history found' message)
    const sessionItems = ul.querySelectorAll('li:not(.text-muted)');
    if (sessionItems.length > 1) {
      event.stopPropagation();
      event.preventDefault();
      const parent_li = el.closest('li');
      htmx.trigger(el, 'hx:delete');
      // Once the HTMX request is sent, we can remove the parent li
      setTimeout(() => {
        if (parent_li && parent_li.parentElement) {
          parent_li.parentElement.removeChild(parent_li);
        }
      }, 100); // Adjust timeout as needed for HTMX request to complete
      return false;
    }
  }
  // Otherwise, let the event bubble (dropdown will close)
}

function showResultIfAvailable() {
  const previewResult = document.getElementById('preview-result');
  if (previewResult && previewResult.innerHTML.trim() === '') {
    const btn = document.querySelector('.template-result-btn');
    if (btn) {
      btn.click();
    }
  }
}
document.addEventListener('DOMContentLoaded', function () {
  showResultIfAvailable();
});
document.body.addEventListener('htmx:afterSwap', function (evt) {
  setTimeout(showResultIfAvailable, 100);
});

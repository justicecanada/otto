function toggleWarning(public_checkbox) {
  if (public_checkbox.checked) {
    document.getElementById("public-library-warning").classList.remove("d-none");
  } else {
    document.getElementById("public-library-warning").classList.add("d-none");
  }
}

// Sub-components (library list, etc) will add click and keydown handlers
selectListItem = (el) => {
  if (el.classList.contains("temp")) {
    return;
  }
  el.parentElement.querySelectorAll("li.temp").forEach(e => e.remove());
  el.parentElement.querySelectorAll('[aria-selected=true]').forEach(e => e.setAttribute('aria-selected', 'false'));
  el.setAttribute('aria-selected', 'true');
};


/*
// Fancy file upload - not implemented yet
// Monitor the document upload form for changes
let documentUploadForm = document.querySelector('#document-upload-form');
documentUploadForm.addEventListener('change', function (e) {
  documentUploadForm.dispatchEvent(new Event('initDocumentUpload'));
});
class DocumentUpload {

  constructor(input, upload_url, message_id) {
    this.input = input;
    this.upload_url = upload_url;
    this.message_id = message_id;
    this.progress_bar = document.querySelector(`#message_${message_id} .progress-bar`);
    this.cur_filename = document.querySelector(`#message_${message_id} .filename`);
    this.cur_filenum = document.querySelector(`#message_${message_id} .filenum`);
    this.total_filenum = document.querySelector(`#message_${message_id} .total-filenum`);
    this.progress_container = document.querySelector(`#message_${message_id} .progress-container`);
    this.max_chunk_size = 1024 * 512; // 512kb
  }

  upload() {
    this.total_filenum.innerHTML = this.input.files.length;
    this.cur_file_idx = 0;
    this.initFileUpload(this.cur_file_idx);
  }

  initFileUpload(i) {
    var file = this.input.files[i];
    this.file = file;
    this.cur_filename.innerHTML = file.name;
    this.cur_filenum.innerHTML = i + 1;
    this.progress_container.classList.remove("d-none");
    scrollToBottom(false);
    this.upload_file(0, null);
  }

  upload_file(start, file_id) {
    var end;
    var self = this;
    var formData = new FormData();
    var nextChunk = start + this.max_chunk_size + 1;
    var currentChunk = this.file.slice(start, nextChunk);
    var uploadedChunk = start + currentChunk.size;
    if (uploadedChunk >= this.file.size) {
      end = 1;
    } else {
      end = 0;
    }
    formData.append('file', currentChunk);
    formData.append('filename', this.file.name);
    formData.append('end', end);
    formData.append('file_id', file_id);
    formData.append('nextSlice', nextChunk);
    formData.append('content_type', this.file.type);
    $.ajaxSetup({
      headers: {
        "X-CSRFToken": document.querySelector('[name=csrfmiddlewaretoken]').value,
      }
    });
    $.ajax({
      xhr: function () {
        var xhr = new XMLHttpRequest();
        xhr.upload.addEventListener('progress', function (e) {
          if (e.lengthComputable) {
            if (self.file.size < self.max_chunk_size) {
              var percent = Math.round((e.loaded / e.total) * 100);
            } else {
              var percent = Math.round((uploadedChunk / self.file.size) * 100);
            }
            self.progress_bar.style.width = percent + "%";
            self.progress_bar.parentElement.setAttribute("aria-valuenow", percent);
          }
        });
        return xhr;
      },

      url: this.upload_url,
      type: 'POST',
      dataType: 'json',
      cache: false,
      processData: false,
      contentType: false,
      data: formData,
      error: function (xhr) {
        alert(xhr.statusText);
      },
      success: function (res) {
        if (nextChunk < self.file.size) {
          // upload file in chunks
          file_id = res.file_id;
          self.upload_file(nextChunk, file_id);
        } else {
          // Upload finished. Upload the next file, if there is one
          self.cur_file_idx++;
          if (self.cur_file_idx < self.input.files.length) {
            // Replace the progress bar with a new one
            let new_progress = self.progress_bar.parentElement.cloneNode(true);
            new_progress.querySelector('.progress-bar').style.width = "0%";
            new_progress.setAttribute("aria-valuenow", 0);
            self.progress_bar.parentElement.replaceWith(new_progress);
            self.progress_bar = new_progress.querySelector('.progress-bar');
            self.initFileUpload(self.cur_file_idx);
          } else {
            // All files uploaded! Trigger the final response
            htmx.trigger(`#message_${self.message_id} .progress-container`, "done_upload");
          }
        }
      }
    });
  };
}

function initDocumentUpload() {
  let upload_url = "{% url 'chat:chunk_upload' message_id=message.id %}";
  let message_id = "{{ message.id }}";
  let file_input = document.querySelector('#chat-file-input');
  let uploader = new FileUpload(file_input, upload_url, message_id);
  uploader.upload();
  // Copy the '.option select' form elements from #prompt-form into this form
  let prompt_form = document.querySelector('#prompt-form');
  let progress_form = document.querySelector('#progress-' + message_id);
  let options = prompt_form.querySelectorAll('.option select');
  for (option of options) {
    let hiddenInput = document.createElement('input');
    hiddenInput.type = 'hidden';
    hiddenInput.name = option.name;
    hiddenInput.value = option.value;
    progress_form.appendChild(hiddenInput);
  }
}

// Drag and drop file upload
function ePrevent(e) {
  e.preventDefault();
  e.stopPropagation();
}
function onDrop(e) {
  e.preventDefault();
  e.stopPropagation();

  document.querySelector('#chat-file-input').value = null;
  document.querySelector('#chat-file-input').files = e.dataTransfer.files;
  document.querySelector('#chat-file-form').dispatchEvent(new Event('change'));
  hideOverlay();
}
function handleDragEnter(e) {
  // If we're in "chat" or "qa" mode, don't do anything
  // TODO: Allow temporary QA libraries for drag-drop into chat
  let mode = document.querySelector('#chat-action').value;
  if (mode == 'chat' || mode == 'qa') {
    return;
  }
  // make sure we're dragging a file
  var dt = (e && e.dataTransfer);
  var isFile = (dt && dt.types && dt.types.length == 1 && dt.types[0] == "Files");
  if (isFile) {
    // and, if so, show the overlay
    showOverlay();
  }
}

function handleDragLeave(e) {
  // was our dragleave off the page?
  if (e && e.pageX == 0 && e.pageY == 0) {
    // then hide the overlay
    hideOverlay();
  }
}

function showOverlay() {
  document.querySelector('#file-dropzone').classList.add('show');
}

function hideOverlay() {
  document.querySelector('#file-dropzone').classList.remove('show');
}

let dropZone = document.querySelector('#file-dropzone');
window.addEventListener('dragenter', handleDragEnter);
dropZone.addEventListener('dragover', ePrevent);
dropZone.addEventListener('dragleave', handleDragLeave);
dropZone.addEventListener('drop', onDrop);

*/

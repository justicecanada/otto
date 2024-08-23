


$(document).ready(function () {

  // Retrieve CSRF token value from hidden input field
  const csrfToken = $('#csrf_token').val();

  // Check if there is an element with ID 'sortable'
  const sortableElement = document.getElementById('sortable');
  if (sortableElement) {
    // Initialize Sortable
    new Sortable(sortableElement, {
      animation: 150,
      handle: '.drag-handle',
      ghostClass: 'sortable-ghost'
    });
  }

  // Function to save documents
  function saveDocuments() {
    const data = [];
    let sequence = 0;

    $('.document-name-input').each(function () {
      sequence++;
      const tr = $(this).closest('tr');
      data.push({
        id: tr.data('id'),
        sequence: sequence,
        name: $(this).val(),
        date: tr.find('input[type="date"]').val(),
        hidden: tr.find('.bi-eye-slash').length > 0
      });
    });

    return new Promise((resolve, reject) => {
      $.ajax({
        url: $('#saveButton').data('save-url'),  // Accessing the save URL from data attribute
        type: 'POST',
        headers: {
          'X-CSRFToken': csrfToken
        },
        data: JSON.stringify(data),
        contentType: 'application/json',
        success: function (data) {
          console.log(data);
          resolve(true); // Resolve the promise if save is successful
        },
        error: function (error) {
          console.error('Error:', error);
          reject(false); // Reject the promise if save fails
        }
      });
    });
  }

  // Function to generate Book of Docs
  function generateBookofDocs() {
    const sessionId = $('#generateDownloadButton').data('session-id');

    $('#generateSpinner').removeClass('d-none');

    $.ajax({
      url: $('#generateDownloadButton').data('generate-url'),  // Accessing the generate URL from data attribute
      type: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken
      },
      data: JSON.stringify({session_id: sessionId}),
      success: function (data) {
        $('#generateSpinner').addClass('d-none');
        if (data.url) {
          window.location.href = data.url;
        } else {
          alert("Error generating book of documents.");
        }
      },
      error: function (error) {
        console.error('Error:', error);
        $('#generateSpinner').addClass('d-none');
      }
    });
  }

  // Function to create Table of Contents
  function createTableOfContents() {
    const sessionId = $('#createTocButton').data('session-id');

    $.ajax({
      url: $('#createTocButton').data('generate-url'),  // Accessing the generate URL from data attribute
      type: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken
      },
      data: JSON.stringify({session_id: sessionId}),
      success: function (data) {
        window.location.reload(); // Reload the page after creating Table of Contents
      },
      error: function (error) {
        console.error('Error:', error);
      }
    });
  }

  // Event delegation for document upload form submission
  $(document).on('submit', '#uploadForm', function (event) {
    event.preventDefault(); // Prevent the default form submission behavior

    const form = $(this);
    const uploadButton = form.find('button[type="submit"]'); // Get the upload button

    // Show spinner inside the upload button
    uploadButton.html('<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Uploading...');

    // Submit the form asynchronously using jQuery
    $.ajax({
      url: form.attr('action'), // Get the form action URL
      type: form.attr('method'), // Get the form method (POST)
      data: new FormData(form[0]), // Create a FormData object from the form data
      processData: false, // Prevent jQuery from automatically processing the data
      contentType: false, // Prevent jQuery from automatically setting the content type
      headers: {
        'X-CSRFToken': csrfToken
      },
      success: function (data, textStatus, jqXHR) {
        // Reload the page to display the newly uploaded documents and apply the sortable functionality
        // Ensure it refreshes properly though and add a slight delay
        setTimeout(() => {
          window.location.reload();
        }, 500);
      },
      error: function (jqXHR, textStatus, errorThrown) {
        console.error('Error:', errorThrown); // Log any errors
        uploadButton.text('Upload'); // Reset the upload button text
      }
    });
  });


  // Event delegation for document delete button
  $(document).on('click', '.document-delete-button', function () {
    //const tr = $(this).closest('tr');
    //const documentId = tr.data('id');
    const trs = getSelectedDocumentRows();
    const documentIds = getSelectedDocuments();

    $.ajax({
      url: tr.data('delete-url'),  // Accessing the delete URL from data attribute
      type: 'POST',
      headers: {
        'X-CSRFToken': csrfToken
      },
      data: JSON.stringify({selected_documents: documentIds}),
      contentType: 'application/json',
      beforeSend: function () {
        $('#deleteButton').hide();
        $('#loadingButton').show();
      },
      success: function (data) {
        console.log(data);
        trs.each(function () {
          $(this).remove(); // Remove each selected row
        });
        alert('Documents deleted successfully!');
        $('#loadingButton').hide();
        $('#deleteButton').show();
        location.reload();
      },
      error: function (error) {
        console.error('Error:', error);
        $('#loadingButton').hide();
        $('#deleteButton').show();
      }
    });
  });
  function getSelectedDocumentRows() {
    return $('tr.selected'); // Assuming rows have a 'selected' class when selected
  }
  // Event delegation for document visibility toggle
  $(document).on('click', '.document-hide-button', function () {
    const button = $(this);
    // const documentId = button.data('id');
    // const icon = button.find('i');
    const documentIds = getSelectedDocuments();
    if (documentIds.length === 0) {
      alert("No documents selected.");
      return;
    }
    $.ajax({
      url: button.data('toggle-url'),  // Accessing the toggle URL from data attribute
      type: 'POST',
      headers: {
        'X-CSRFToken': csrfToken
      },
      data: JSON.stringify({selected_documents: documentIds}), //document_id: documentId}),
      contentType: 'application/json',
      success: function (data) {
        //icon.toggleClass('bi-eye-slash bi-eye');
        alert(data.message);
        // Optionally, update the icons for the selected documents
        documentIds.forEach(id => {
          const icon = $(`tr[data-id="${id}"]`).find('i');
          icon.toggleClass('bi-eye-slash bi-eye');
        });
      },
      error: function (error) {
        console.error('Error:', error);
      }
    });
  });

  // Save button click event
  $('#saveButton').click(function () {
    // Call the saveDocuments function
    saveDocuments().then(function (success) {
      console.log('Save operation successful');
    }).catch(function (error) {
      console.error('Save operation failed');
    });
  });

  // Event delegation for dropdown button
  $(document).on('click', '#sortable button.dropdown-toggle', function () {
    // Hide all other dropdown menus
    $('.dropdown-menu').removeClass('show');
    const dropdownMenu = $(this).next('.dropdown-menu');
    dropdownMenu.toggleClass('show');
  });

  // Event listener for delete button click
  $('#deleteButton').click(function () {
    if (confirm("Are you sure you want to delete this session?")) {
      $.ajax({
        url: $(this).data('delete-url'),  // Accessing the delete URL from data attribute
        type: 'DELETE',
        headers: {
          'X-CSRFToken': csrfToken
        },
        success: function (data) {
          //location.reload();
          window.location.href = data.url; // Replace with your delete redirect URL
        },
        error: function (error) {
          console.error('Error:', error);
        }
      });
    }
  });

  // Event listener for generate and download button click
  $('#generateDownloadButton').click(async function (event) {
    event.preventDefault();
    try {
      // Save the documents
      const saveResult = await saveDocuments();
      if (saveResult) {
        // If save is successful, create the Table of Contents
        generateBookofDocs();
      }
    } catch (error) {
      console.error('Error saving documents:', error);
    }

  });

  // Event listener for generate and download button click
  $('#createTocButton').click(async function (event) {
    event.preventDefault();
    try {
      // Save the documents
      const saveResult = await saveDocuments();
      if (saveResult) {
        // If save is successful, create the Table of Contents
        createTableOfContents();
      }
    } catch (error) {
      console.error('Error saving documents:', error);
    }

  });

  $('.btn-upvote').click(function () {
    var form = $(this).closest('form');
    var formData = form.serialize();
    var messageDiv = form.find('.message');

    $.ajax({
      type: 'POST',
      url: form.attr('action'),
      data: formData,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken
      },
      success: function (data) {
        messageDiv.text(data.message);
        messageDiv.removeClass('alert-danger').addClass('alert alert-success');
      },
      error: function (xhr, status, error) {
        messageDiv.text(data.message);
        messageDiv.removeClass('alert-success').addClass('alert alert-danger');
      }
    });
  });

});
$(document).on('click', '[data-bs-target="#summarizationModal"]', function () {
  const selectedDocumentIds = [];
  $('input[name="selected_documents"]:checked').each(function () {
    selectedDocumentIds.push($(this).val());
  });
  $('#summarizationModal').data('document-ids', selectedDocumentIds);
});

//$(document).on('click', '#summarizeForm button[type="submit"]', function (e) {
$(document).on('submit', '#summarizeForm', function (e) {
  e.preventDefault();
  const form = $('#summarizeForm');
  //const documentId = $('#summarizationModal').data('document-id');
  const selectedDocumentIds = $('#summarizationModal').data('document-ids');

  const requestData = {
    document_ids: selectedDocumentIds,
    length: form.find('select[name="length"]').val(),
    target_language: form.find('select[name="target_language"]').val()
  };

  console.log('Sending request data:', requestData);  // Add this line for debugging

  $.ajax({
    url: '/case_prep/summarize_feature/', //form.attr('action'), //
    type: 'POST',
    headers: {
      'X-CSRFToken': $('[name=csrfmiddlewaretoken]').val(),
      'Content-Type': 'application/json'
    },
    data: JSON.stringify(requestData),
    beforeSend: function () {
      $('#summaryResult').html('Summary loading...');
      $('#generateButton').hide();
      $('#loadingButton').show();
    },
    success: function (data) {
      console.log('Summarization successful:', data);
      $('#summaryResult').html('Summary Document Created!');
      $('#loadingButton').hide();
      $('#generateButton').show();
      location.reload();
    },
    error: function (error) {
      console.error('Summarization error:', error);
      $('#loadingButton').hide();
      $('#generateButton').show();
    }
  });
});
$(document).on('click', '#closeButton', function () {
  console.clear();
});


$(document).on('click', '[data-bs-target="#translationModal"]', function () {
  const selectedDocumentIds = [];
  $('input[name="selected_documents"]:checked').each(function () {
    selectedDocumentIds.push($(this).val());
  });
  $('#translationModal').data('document-ids', selectedDocumentIds);
});

$(document).on('click', '#translateForm button[type="submit"]', function (e) {
  e.preventDefault();
  const form = $('#translateForm');
  const selectedDocumentIds = $('#translationModal').data('document-ids');

  const requestData = {
    document_ids: selectedDocumentIds,
    target_language: form.find('select[name="target_language"]').val()
  };

  console.log('Sending request data:', requestData);  // Add this line for debugging
  // Show the loading button and hide the generate button


  $.ajax({
    url: '/case_prep/translate_feature/',
    type: 'POST',
    headers: {
      'X-CSRFToken': $('[name=csrfmiddlewaretoken]').val(),
      'Content-Type': 'application/json'
    },
    data: JSON.stringify(requestData),
    beforeSend: function () {
      // Show "Result loading..." message
      $('#translateResult').html('Translation loading...');
      // Hide the generate button and show the loading button
      $('#generateButton').hide();
      $('#loadingButton').show();
    },
    success: function (data) {
      console.log('Translation successful:', data);
      $('#translateResult').html('Translation Document Created!'); //data.translated_text);
      // Hide the loading button and show the generate button
      $('#loadingButton').hide();
      $('#generateButton').show();
    },
    error: function (error) {
      console.error('Translation error:', error);
      $('#loadingButton').hide();
      $('#generateButton').show();
    }
  });
});


function toggleSelectAll(source) {
  const checkboxes = document.querySelectorAll('tbody input[name="selected_documents"]');
  checkboxes.forEach(checkbox => {
    checkbox.checked = source.checked;
  });
}
function getSelectedDocuments() {
  const selectedDocuments = [];
  $('input[name="selected_documents"]:checked').each(function () {
    selectedDocuments.push($(this).data('document-id'));
  });
  return selectedDocuments;
}

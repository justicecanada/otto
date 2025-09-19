// Chat options: handle library / data source etc. selection changes

function updateLibraryModalButton() {
  const selectElement = document.getElementById('id_qa_library');
  const selectedLibraryId = selectElement.value;
  const buttonElement = document.getElementById('editLibrariesButton');
  buttonElement.setAttribute('hx-get', `/librarian/modal/library/${selectedLibraryId}/edit/`);
  htmx.process(buttonElement);
}

function updateQaSourceForms() {
  const scope = document.getElementById('id_qa_scope').value;
  const dataSources = document.getElementById('qa_data_sources_autocomplete');
  const documents = document.getElementById('qa_documents_autocomplete');
  if (scope === 'data_sources') {
    dataSources.classList.remove('d-none');
    documents.classList.add('d-none');
  } else if (scope === 'documents') {
    dataSources.classList.add('d-none');
    documents.classList.remove('d-none');
  } else {
    dataSources.classList.add('d-none');
    documents.classList.add('d-none');
  }
}

function updateTranslateForms() {
  const modelSelect = document.getElementById('id_translate_model');
  const promptContainer = document.getElementById('translate-prompt-container');
  const glossaryContainer = document.getElementById('glossary-upload-fragment');
  const selectedOption = modelSelect.options[modelSelect.selectedIndex];
  // For "gpt" models, show the translate-prompt input. They will have "gpt" in their model ID.
  if (selectedOption && selectedOption.value.includes('gpt')) {
    promptContainer.style.display = 'block';
    glossaryContainer.style.display = 'none';
  } else {
    promptContainer.style.display = 'none';
    glossaryContainer.style.display = 'block';
  }
}

// TODO: abstract this into a JS helper class for Autocomplete widgets
// and contribute upstream to django-htmx-autocomplete repo
function updateAutocompleteLibraryid(element_id) {
  // Add hx-vals to the autocomplete elements
  const input_element = document.getElementById(element_id);

  // hx-vals attribute already has a value, e.g.
  // js:{name: 'qa_data_sources', component_id: 'id_qa_data_sources', search: document.getElementById('id_qa_data_sources__textinput').value}
  let hx_vals = input_element.getAttribute('hx-vals');
  // Remove the last character, which is a closing brace
  hx_vals = hx_vals.slice(0, -1);
  // Now add the library_id key to the hx-vals string
  hx_vals += ", library_id: document.getElementById('id_qa_library').value, chat_id: chat_id}";
  input_element.setAttribute('hx-vals', hx_vals);
}

// After toggling elements in the autocomplete widget, the input element is swapped out.
// Monitor the related input elements for hx-swaps and then update the library ID again.
document.addEventListener("htmx:afterSettle", function (event) {
  if (event.target.id == "id_qa_data_sources" || event.target.id == "id_qa_documents") {
    updateAutocompleteLibraryid('id_qa_data_sources__textinput');
    updateAutocompleteLibraryid('id_qa_documents__textinput');
    // Unlike the other widgets, the autocomplete doesn't have a change event to trigger
    // the ChatOption form save, but we can trigger it now
    triggerOptionSave();
  }
});

function clearAutocomplete(field_name) {
  const input_wrapper = document.querySelector(`#id_${field_name}`);
  const result_items = document.querySelector(`#id_${field_name}__items`);
  const chips = document.querySelectorAll(`#id_${field_name}_ac_container li.chip`);
  const info = document.querySelector(`#id_${field_name}__info`);
  const sr_desc = document.querySelector(`#id_${field_name}__sr_description`);
  input_wrapper.innerHTML = '';
  result_items.innerHTML = '';
  chips.forEach(chip => chip.remove());
  info.innerHTML = '';
  sr_desc.innerHTML = '';
}

function resetQaElements() {
  document.getElementById('id_qa_mode').value = 'rag';
  document.getElementById('id_qa_process_mode').value = 'combined_docs';
  document.getElementById('id_qa_scope').value = 'all';
}

function resetQaAutocompletes() {
  updateQaSourceForms();
  clearAutocomplete('qa_data_sources');
  clearAutocomplete('qa_documents');
}

function switchToDocumentScope() {
  const scope = document.getElementById('id_qa_scope');
  if (scope.value === "all") {
    scope.value = "documents";
  }
}

function toggleReasoningEffort() {
  const modelSelect = document.getElementById('id_chat_model');
  const reasoningEffortContainer = document.getElementById('reasoning-effort-container');
  const selectedOption = modelSelect.options[modelSelect.selectedIndex];

  if (selectedOption && selectedOption.dataset.isReasoning === 'true') {
    reasoningEffortContainer.style.display = 'block';
  } else {
    reasoningEffortContainer.style.display = 'none';
  }
}

function toggleQaReasoningEffort() {
  const modelSelect = document.getElementById('id_qa_model');
  const reasoningEffortContainer = document.getElementById('qa-reasoning-effort-container');

  if (!modelSelect || !reasoningEffortContainer) {
    return;
  }

  const selectedOption = modelSelect.options[modelSelect.selectedIndex];

  if (selectedOption && selectedOption.dataset.isReasoning === 'true') {
    reasoningEffortContainer.style.display = 'block';
  } else {
    reasoningEffortContainer.style.display = 'none';
  }
}

document.addEventListener('DOMContentLoaded', function () {
  toggleReasoningEffort();
  toggleQaReasoningEffort();

  const modelSelect = document.getElementById('id_chat_model');
  if (modelSelect) {
    modelSelect.addEventListener('change', toggleReasoningEffort);
  }

  const qaModelSelect = document.getElementById('id_qa_model');
  if (qaModelSelect) {
    qaModelSelect.addEventListener('change', toggleQaReasoningEffort);
  }
});

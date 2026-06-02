// Chat options: handle library / data source etc. selection changes

function setElementVisible(element, isVisible) {
  if (!element) return;
  element.classList.toggle('d-none', !isVisible);
}

function updateLibraryModalButton() {
  const selectElement = document.getElementById('id_qa_library');
  if (!selectElement) return;
  const selectedLibraryId = selectElement.value;
  const buttonElement = document.getElementById('editLibrariesButton');
  if (!buttonElement) return;
  buttonElement.setAttribute('hx-get', `/librarian/modal/library/${selectedLibraryId}/edit/`);
  htmx.process(buttonElement);
}

function updateQaSourceForms() {
  const scopeField = document.getElementById('id_qa_scope');
  if (!scopeField) return;
  const scope = scopeField.value;
  const dataSources = document.getElementById('qa_data_sources_autocomplete');
  const documents = document.getElementById('qa_documents_autocomplete');
  const excludedDocuments = document.getElementById('qa_excluded_documents_autocomplete');
  const additionalDocuments = document.getElementById('qa_additional_documents_autocomplete');
  setElementVisible(dataSources, scope === 'data_sources');
  setElementVisible(excludedDocuments, scope === 'data_sources');
  setElementVisible(additionalDocuments, scope === 'data_sources');
  setElementVisible(documents, scope === 'documents');
}

function updateTranslateForms() {
  const modelSelect = document.getElementById('id_translate_model');
  if (!modelSelect) return;
  const promptContainer = document.getElementById('translate-prompt-container');
  const glossaryContainer = document.getElementById('glossary-upload-fragment');
  const selectedOption = modelSelect.options[modelSelect.selectedIndex];
  // For "gpt" models, show the translate-prompt input. They will have "gpt" in their model ID.
  const showPrompt = Boolean(selectedOption && selectedOption.value.includes('gpt'));
  setElementVisible(promptContainer, showPrompt);
  setElementVisible(glossaryContainer, !showPrompt);
}

function applyModelConditionals() {
  toggleReasoningEffort();
  toggleVerbosity();
  toggleQaReasoningEffort();
  toggleQaVerbosity();
  toggleSummarizeReasoningEffort();
  toggleSummarizeVerbosity();
}

function initializeChatOptionsConditionalUI() {
  updateLibraryModalButton();
  updateQaSourceForms();
  updateTranslateForms();
  applyModelConditionals();
  updateAutocompleteLibraryid('id_qa_data_sources__textinput');
  updateAutocompleteLibraryid('id_qa_documents__textinput');
  updateAutocompleteLibraryid('id_qa_additional_documents__textinput');
  updateAutocompleteLibraryid('id_qa_excluded_documents__textinput');
}

// Expose globally for inline template script and scripts.js lifecycle hooks
window.initializeChatOptionsConditionalUI = initializeChatOptionsConditionalUI;

if (!window.chatOptionsChangeHandlerBound) {
  document.addEventListener('change', function (event) {
    const targetId = event.target?.id;

    if (targetId === 'id_chat_model') {
      toggleReasoningEffort();
      toggleVerbosity();
    } else if (targetId === 'id_qa_model') {
      toggleQaReasoningEffort();
      toggleQaVerbosity();
    } else if (targetId === 'id_summarize_model') {
      toggleSummarizeReasoningEffort();
      toggleSummarizeVerbosity();
    } else if (targetId === 'id_translate_model') {
      updateTranslateForms();
    } else if (targetId === 'id_qa_scope') {
      updateQaSourceForms();
    } else if (targetId === 'id_qa_library') {
      updateLibraryModalButton();
    }
  });
  window.chatOptionsChangeHandlerBound = true;
}

document.addEventListener('DOMContentLoaded', function () {
  initializeChatOptionsConditionalUI();
});

// Re-apply conditional state whenever options accordion HTML is swapped.
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id === 'options-accordion') {
    initializeChatOptionsConditionalUI();
  }
});

document.addEventListener('htmx:oobAfterSwap', function (event) {
  if (event.detail?.target?.id === 'options-accordion') {
    initializeChatOptionsConditionalUI();
  }
});

// Re-apply translate conditional state when glossary fragment is updated on its own.
document.addEventListener('htmx:oobAfterSwap', function (event) {
  if (event.detail?.target?.id === 'glossary-upload-fragment') {
    updateTranslateForms();
  }
});

// Called when a model select changes. Normalizes the corresponding reasoning effort
// BEFORE triggering the form save. This ensures the correct value is submitted.
function normalizeAndSave(modelFieldName) {
  // Map model field to its corresponding reasoning effort and verbosity fields/functions
  if (modelFieldName === 'chat_model') {
    toggleReasoningEffort();
    toggleVerbosity();
  } else if (modelFieldName === 'summarize_model') {
    toggleSummarizeReasoningEffort();
    toggleSummarizeVerbosity();
  } else if (modelFieldName === 'qa_model') {
    toggleQaReasoningEffort();
    toggleQaVerbosity();
  }
  // Now trigger the save with the normalized values
  triggerOptionSave();
}

// Normalize reasoning effort so that the selected value is valid for the chosen model.
// Supported efforts are provided by the model <option> via data-supported-reasoning-efforts.
function normalizeReasoningEffort(selectElement, modelValue) {
  if (!selectElement) return;

  const selectedOption = selectElement.closest('form')?.querySelector(`option[value="${modelValue}"]`);
  const supportedEfforts = String(selectedOption?.dataset.supportedReasoningEfforts || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  let value = selectElement.value;

  if (!supportedEfforts.length) return;

  if (supportedEfforts.includes(value)) {
    // keep as-is
  } else if (value === 'minimal' && supportedEfforts.includes('none')) {
    value = 'none';
  } else if (value === 'none' && supportedEfforts.includes('minimal')) {
    value = 'minimal';
  } else if (value === 'xhigh' && supportedEfforts.includes('high')) {
    value = 'high';
  } else {
    [value] = supportedEfforts;
  }

  selectElement.value = value;

  Array.from(selectElement.options).forEach((opt) => {
    opt.hidden = supportedEfforts.length ? !supportedEfforts.includes(opt.value) : false;
  });
}

// TODO: abstract this into a JS helper class for Autocomplete widgets
// and contribute upstream to django-htmx-autocomplete repo
function updateAutocompleteLibraryid(element_id) {
  // Add hx-vals to the autocomplete elements
  const input_element = document.getElementById(element_id);
  if (!input_element) return;

  // hx-vals attribute already has a value, e.g.
  // js:{name: 'qa_data_sources', component_id: 'id_qa_data_sources', search: document.getElementById('id_qa_data_sources__textinput').value}
  let hx_vals = input_element.getAttribute('hx-vals');
  if (!hx_vals || !hx_vals.endsWith('}')) return;
  // Remove the last character, which is a closing brace
  hx_vals = hx_vals.slice(0, -1);
  // Now add the library_id key to the hx-vals string
  hx_vals += ", library_id: document.getElementById('id_qa_library').value, chat_id: chat_id, selected_data_source_ids: Array.from(document.querySelectorAll('#id_qa_data_sources input[type=\"hidden\"][name=\"qa_data_sources\"]')).map((el) => el.value).join(',')}";
  input_element.setAttribute('hx-vals', hx_vals);
}

// After toggling elements in the autocomplete widget, the input element is swapped out.
// Monitor the related input elements for hx-swaps and then update the library ID again.
document.addEventListener("htmx:afterSettle", function (event) {
  if (
    event.target.id == "id_qa_data_sources"
    || event.target.id == "id_qa_documents"
    || event.target.id == "id_qa_additional_documents"
    || event.target.id == "id_qa_excluded_documents"
  ) {
    updateAutocompleteLibraryid('id_qa_data_sources__textinput');
    updateAutocompleteLibraryid('id_qa_documents__textinput');
    updateAutocompleteLibraryid('id_qa_additional_documents__textinput');
    updateAutocompleteLibraryid('id_qa_excluded_documents__textinput');
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
  if (!input_wrapper || !result_items || !info || !sr_desc) {
    return;
  }
  input_wrapper.innerHTML = '';
  result_items.innerHTML = '';
  chips.forEach(chip => chip.remove());
  info.innerHTML = '';
  sr_desc.innerHTML = '';
}

function resetQaAutocompletes() {
  updateQaSourceForms();
  clearAutocomplete('qa_data_sources');
  clearAutocomplete('qa_documents');
  clearAutocomplete('qa_additional_documents');
  clearAutocomplete('qa_excluded_documents');
}

function toggleReasoningEffort() {
  const modelSelect = document.getElementById('id_chat_model');
  const reasoningEffortContainer = document.getElementById('reasoning-effort-container');
  const temperatureContainer = document.getElementById('temperature-container');
  const reasoningSelect = document.getElementById('id_chat_reasoning_effort');
  if (!modelSelect || !reasoningEffortContainer) {
    return;
  }
  const selectedOption = modelSelect.options[modelSelect.selectedIndex];

  if (selectedOption && selectedOption.dataset.isReasoning === 'true') {
    setElementVisible(reasoningEffortContainer, true);
    normalizeReasoningEffort(reasoningSelect, selectedOption.value);
    setElementVisible(temperatureContainer, false);
  } else {
    setElementVisible(reasoningEffortContainer, false);
    setElementVisible(temperatureContainer, true);
  }
}

function toggleVerbosity() {
  const modelSelect = document.getElementById('id_chat_model');
  const verbosityContainer = document.getElementById('verbosity-container');

  if (!modelSelect || !verbosityContainer) {
    return;
  }

  const selectedOption = modelSelect.options[modelSelect.selectedIndex];
  const modelValue = selectedOption ? selectedOption.value : '';

  // Show verbosity only for gpt-5 models
  if (modelValue.startsWith('gpt-5')) {
    setElementVisible(verbosityContainer, true);
  } else {
    setElementVisible(verbosityContainer, false);
  }
}

function toggleQaReasoningEffort() {
  const modelSelect = document.getElementById('id_qa_model');
  const reasoningEffortContainer = document.getElementById('qa-reasoning-effort-container');
  const reasoningSelect = document.getElementById('id_qa_reasoning_effort');

  if (!modelSelect || !reasoningEffortContainer) {
    return;
  }

  const selectedOption = modelSelect.options[modelSelect.selectedIndex];

  if (selectedOption && selectedOption.dataset.isReasoning === 'true') {
    setElementVisible(reasoningEffortContainer, true);
    normalizeReasoningEffort(reasoningSelect, selectedOption.value);
  } else {
    setElementVisible(reasoningEffortContainer, false);
  }
}

function toggleQaVerbosity() {
  const modelSelect = document.getElementById('id_qa_model');
  const verbosityContainer = document.getElementById('qa-verbosity-container');

  if (!modelSelect || !verbosityContainer) {
    return;
  }

  const selectedOption = modelSelect.options[modelSelect.selectedIndex];
  const modelValue = selectedOption ? selectedOption.value : '';

  // Show verbosity only for gpt-5 models
  if (modelValue.startsWith('gpt-5')) {
    setElementVisible(verbosityContainer, true);
  } else {
    setElementVisible(verbosityContainer, false);
  }
}

function toggleSummarizeReasoningEffort() {
  const modelSelect = document.getElementById('id_summarize_model');
  const reasoningEffortContainer = document.getElementById('summarize-reasoning-effort-container');
  const reasoningSelect = document.getElementById('id_summarize_reasoning_effort');
  if (!modelSelect || !reasoningEffortContainer) {
    return;
  }
  const selectedOption = modelSelect.options[modelSelect.selectedIndex];

  if (selectedOption && selectedOption.dataset.isReasoning === 'true') {
    setElementVisible(reasoningEffortContainer, true);
    normalizeReasoningEffort(reasoningSelect, selectedOption.value);
  } else {
    setElementVisible(reasoningEffortContainer, false);
  }
}

function toggleSummarizeVerbosity() {
  const modelSelect = document.getElementById('id_summarize_model');
  const verbosityContainer = document.getElementById('summarize-verbosity-container');

  if (!modelSelect || !verbosityContainer) {
    return;
  }

  const selectedOption = modelSelect.options[modelSelect.selectedIndex];
  const modelValue = selectedOption ? selectedOption.value : '';

  // Show verbosity only for gpt-5 models
  if (modelValue.startsWith('gpt-5')) {
    setElementVisible(verbosityContainer, true);
  } else {
    setElementVisible(verbosityContainer, false);
  }
}

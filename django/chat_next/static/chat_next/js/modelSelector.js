(function () {
  function trimCompactLabel(text) {
    return String(text || '').split('(')[0].trim();
  }

  function setElementVisible(element, isVisible) {
    if (!element) return;
    element.classList.toggle('d-none', !isVisible);
  }

  function normalizeReasoningEffort(selectElement, modelValue) {
    if (!selectElement) return;

    const selectedOption = selectElement.closest('[data-model-controls-root="true"]')
      ?.querySelector('[data-model-select="true"]')?.selectedOptions?.[0];
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

  function updateModelControls(root) {
    if (!root) return;

    const modelSelect = root.querySelector('[data-model-select="true"]');
    const reasoningSelect = root.querySelector('[data-reasoning-select="true"]');
    const reasoningContainer = root.querySelector('[data-model-control="reasoning"]');
    const verbosityContainer = root.querySelector('[data-model-control="verbosity"]');
    const temperatureContainer = root.querySelector('#temperature-container');

    if (!modelSelect) return;

    const selectedOption = modelSelect.options[modelSelect.selectedIndex];
    const modelValue = selectedOption ? selectedOption.value : '';
    const isReasoningModel = selectedOption && selectedOption.dataset.isReasoning === 'true';

    setElementVisible(reasoningContainer, isReasoningModel);
    setElementVisible(verbosityContainer, modelValue.startsWith('gpt-5'));

    if (temperatureContainer) {
      setElementVisible(temperatureContainer, !isReasoningModel);
    }

    if (isReasoningModel) {
      normalizeReasoningEffort(reasoningSelect, modelValue);
    }
  }

  function updateSelectorSummary(selectorRoot) {
    if (!selectorRoot) return;

    const modelSelect = selectorRoot.querySelector('[data-model-select="true"]');
    const reasoningSelect = selectorRoot.querySelector('[data-reasoning-select="true"]');
    const modelLabel = selectorRoot.querySelector('[data-model-summary-name]');
    const reasoningLabel = selectorRoot.querySelector('[data-model-summary-reasoning]');

    if (!modelSelect || !modelLabel || !reasoningLabel) return;

    const selectedOption = modelSelect.options[modelSelect.selectedIndex];
    const reasoningOption = reasoningSelect?.options?.[reasoningSelect.selectedIndex];
    const isReasoningModel = selectedOption && selectedOption.dataset.isReasoning === 'true';
    const standardReasoningLabel = selectorRoot.dataset.standardReasoningLabel || 'Standard';
    const reasoningText = isReasoningModel
      ? trimCompactLabel(reasoningOption?.textContent || '')
      : standardReasoningLabel;

    modelLabel.textContent = trimCompactLabel(selectedOption?.textContent || '');
    reasoningLabel.textContent = `(${reasoningText})`;
  }

  function getDropdownInstance(selectorRoot) {
    const toggle = selectorRoot?.querySelector('[data-bs-toggle="dropdown"]');
    if (!toggle || typeof bootstrap === 'undefined') return null;
    return bootstrap.Dropdown.getOrCreateInstance(toggle, {autoClose: false});
  }

  function hideModelSelector(selectorRoot) {
    const dropdownInstance = getDropdownInstance(selectorRoot);
    if (!dropdownInstance) return;
    dropdownInstance.hide();
  }

  function bindModelControlRoot(root) {
    if (!root || root.dataset.modelControlsInitialized === 'true') return;

    const modelSelect = root.querySelector('[data-model-select="true"]');
    const reasoningSelect = root.querySelector('[data-reasoning-select="true"]');
    const verbositySelect = root.querySelector('[data-verbosity-select="true"]');
    const selectorRoot = root.closest('.chat-model-selector') || root.querySelector('.chat-model-selector');
    const selectorMenu = selectorRoot?.querySelector('.chat-model-selector-menu');

    const refresh = function () {
      updateModelControls(root);
      if (selectorRoot) {
        updateSelectorSummary(selectorRoot);
      }
    };

    [modelSelect, reasoningSelect, verbositySelect].forEach((element) => {
      if (!element) return;
      element.addEventListener('change', refresh);
    });

    ['click', 'mousedown', 'mouseup', 'pointerdown', 'pointerup'].forEach((eventName) => {
      selectorMenu?.addEventListener(eventName, function (event) {
        event.stopPropagation();
      });
    });

    selectorRoot?.addEventListener('hide.bs.dropdown', function (event) {
      if (event.clickEvent && selectorMenu?.contains(event.clickEvent.target)) {
        event.preventDefault();
      }
    });

    refresh();
    root.dataset.modelControlsInitialized = 'true';
  }

  function initializeChatNextModelControls(scope) {
    const root = scope || document;

    if (root.matches?.('[data-model-controls-root="true"]')) {
      bindModelControlRoot(root);
      return;
    }

    root.querySelectorAll?.('[data-model-controls-root="true"]').forEach(bindModelControlRoot);
  }

  window.initializeChatNextModelControls = initializeChatNextModelControls;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initializeChatNextModelControls(document);
    });
  } else {
    initializeChatNextModelControls(document);
  }

  document.addEventListener('htmx:afterSwap', function (event) {
    const target = event.detail?.target;
    if (!target) return;
    initializeChatNextModelControls(target);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;

    document.querySelectorAll('.chat-model-selector').forEach((selectorRoot) => {
      hideModelSelector(selectorRoot);
    });
  });
})();

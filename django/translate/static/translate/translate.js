function setActiveTab(tabButton) {
  const tabs = document.querySelectorAll('#translationTabs .nav-link');
  tabs.forEach((tab) => {
    tab.classList.remove('active');
    tab.classList.remove('fw-semibold');
  });
  tabButton.classList.add('active');
  tabButton.classList.add('fw-semibold');
}

function copyMessage(btn) {
  const container = btn.closest('#translatedTextContainer') || document;
  const messageText = container.querySelector('#translatedText');
  if (!messageText) {
    return;
  }

  navigator.clipboard.writeText(messageText.innerText || messageText.textContent || '');
  btn.blur();
  btn.classList.add('clicked');
  if (btn.dataset.copiedLabel) {
    btn.setAttribute('aria-label', btn.dataset.copiedLabel);
    btn.setAttribute('title', btn.dataset.copiedLabel);
  }
  setTimeout(function () {
    btn.classList.remove('clicked');
    if (btn.dataset.copyLabel) {
      btn.setAttribute('aria-label', btn.dataset.copyLabel);
      btn.setAttribute('title', btn.dataset.copyLabel);
    }
  }, 2200);
}

function downloadAllDocumentResults(trigger) {
  const container = trigger.closest('#document-result') || document;
  const downloadLinks = Array.from(container.querySelectorAll('.translate-download-link'));

  if (downloadLinks.length < 2) {
    return;
  }

  downloadLinks.forEach(function (link, index) {
    setTimeout(function () {
      link.click();
    }, index * 100);
  });
}

function updateTranslatedTextCopyButton() {
  const copyButton = document.getElementById('translatedTextCopyButton');
  const translatedText = document.getElementById('translatedText');

  if (!copyButton || !translatedText) {
    return;
  }

  const hasError = Boolean(translatedText.querySelector('.text-danger'));
  const hasText = Boolean(translatedText.innerText.trim());

  copyButton.classList.toggle('d-none', !hasText || hasError);
}

document.addEventListener('DOMContentLoaded', function () {
  const fileInput = document.getElementById('fileInput');
  const fileNameDisplay = document.getElementById('file-name-display');
  const dropZone = document.getElementById('drop_zone');

  if (fileInput && fileNameDisplay) {
    fileInput.addEventListener('change', function () {
      if (!fileInput.files || fileInput.files.length === 0) {
        fileNameDisplay.textContent = 'No files selected';
        return;
      }

      if (fileInput.files.length === 1) {
        fileNameDisplay.textContent = fileInput.files[0].name;
        return;
      }

      fileNameDisplay.textContent = `${fileInput.files.length} files selected`;
    });
  }

  if (dropZone && fileInput) {
    dropZone.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        fileInput.click();
      }
    });
  }

  const documentTab = document.getElementById('document-tab');
  const textTab = document.getElementById('text-tab');

  if (documentTab) {
    documentTab.addEventListener('click', function () {
      setActiveTab(documentTab);
    });
  }

  if (textTab) {
    textTab.addEventListener('click', function () {
      setActiveTab(textTab);
    });
  }

  document.body.addEventListener('htmx:afterSwap', function (event) {
    if (event.target && event.target.id === 'translatedTextContainer') {
      updateTranslatedTextCopyButton();
    }
  });

  updateTranslatedTextCopyButton();

  const switchBtn = document.getElementById('switchLangBtn');
  if (!switchBtn) return;

  switchBtn.addEventListener('click', function () {
    const sourceLangInput = document.getElementById('sourceLang');
    const targetLangInput = document.getElementById('targetLang');
    const sourceLabel = document.getElementById('source-label');
    const targetLabel = document.getElementById('target-label');
    const sourceTextarea = document.getElementById('sourceText');
    const translatedDiv = document.getElementById('translatedText');

    const tmp = sourceLangInput.value;
    sourceLangInput.value = targetLangInput.value;
    targetLangInput.value = tmp;

    sourceLabel.textContent = sourceLangInput.value === 'fr'
      ? sourceLabel.dataset.labelFr
      : sourceLabel.dataset.labelEn;

    targetLabel.textContent = targetLangInput.value === 'en'
      ? targetLabel.dataset.labelEn
      : targetLabel.dataset.labelFr;

    const tmpText = sourceTextarea.value;
    sourceTextarea.value = translatedDiv.innerText.trim();
    translatedDiv.innerText = tmpText;
    updateTranslatedTextCopyButton();

    if (sourceTextarea.value.trim()) {
      htmx.trigger(sourceTextarea, 'keyup');
    }
  });
});

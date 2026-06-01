// File browser preview, navigation, and preview-content helpers for chat_next.

function downloadMessageFiles(btn) {
  const ul = btn.closest('.file-browser')?.querySelector('ul.list-unstyled');
  if (!ul) return;

  const anchors = Array.from(ul.querySelectorAll('a.file-action-btn[href*="/file/"]'));
  const downloadAnchors = anchors.filter(a => a.querySelector('.bi-download'));
  if (!downloadAnchors.length) return;

  btn.disabled = true;

  downloadAnchors.forEach(a => {
    try {
      a.setAttribute('download', '');
    } catch (e) {
      // noop
    }
  });

  downloadAnchors.forEach((a, i) => {
    setTimeout(() => {
      try {
        a.click();
      } catch (e) {
        window.open(a.href, '_blank');
      }
      if (i === downloadAnchors.length - 1) {
        btn.disabled = false;
      }
    }, i * 250);
  });
}

/* =========================================================================
   File preview navigation
   ========================================================================= */

/**
 * Get the file metadata array for a message from the embedded <script> tag.
 * @param {string} messageId
 * @returns {Array<{id:number, filename:string, downloadUrl:string, previewUrl:string}>}
 */
function _getFileData(messageId) {
  const script = document.getElementById(`file-data-${messageId}`);
  if (!script) return [];
  try {
    return JSON.parse(script.textContent);
  } catch (e) {
    return [];
  }
}

/**
 * Open file preview for a specific file index in a message's file browser.
 * Hides the file list, shows the preview view, loads preview content via HTMX.
 * @param {string} messageId
 * @param {number} fileIndex - 0-based index into the file list
 */
function openFilePreview(messageId, fileIndex) {
  const browser = document.getElementById(`file-browser-${messageId}`);
  if (!browser) return;

  const files = _getFileData(messageId);
  if (!files.length || fileIndex < 0 || fileIndex >= files.length) return;

  const file = files[fileIndex];
  browser.dataset.currentIndex = fileIndex;

  // Toggle views
  const listView = browser.querySelector('.file-list-view');
  const previewView = browser.querySelector('.file-preview-view');
  if (listView) listView.style.display = 'none';
  if (previewView) previewView.style.display = 'block';

  // Expand the message container for preview
  const stack = browser.closest('.message-stack');
  if (stack) stack.classList.add('file-preview-active');
  scheduleChatLayoutPositionUpdate();

  // Update toolbar info
  const filenameEl = previewView.querySelector('.preview-filename');
  const downloadEl = previewView.querySelector('.preview-download-link');
  const copyDropdown = previewView.querySelector('.preview-copy-dropdown');
  const copyBtn = previewView.querySelector('.preview-copy-btn');
  const positionEl = previewView.querySelector('.preview-position');
  const prevBtn = previewView.querySelector('.preview-prev-btn');
  const nextBtn = previewView.querySelector('.preview-next-btn');

  if (filenameEl) filenameEl.textContent = file.filename;
  if (downloadEl) {downloadEl.href = file.downloadUrl; downloadEl.setAttribute('download', '');}
  if (copyDropdown) {
    copyDropdown.classList.add('d-none');
  }
  if (copyBtn) {
    copyBtn.classList.remove('clicked');
    copyBtn.disabled = true;
  }
  if (positionEl) positionEl.textContent = `${fileIndex + 1}/${files.length}`;
  if (prevBtn) prevBtn.disabled = (fileIndex <= 0);
  if (nextBtn) nextBtn.disabled = (fileIndex >= files.length - 1);

  // Hide prev/next if only one file
  const navEl = previewView.querySelector('.preview-nav');
  if (navEl) navEl.style.display = files.length <= 1 ? 'none' : '';

  // Update library icon in toolbar
  const libIcon = previewView.querySelector('.preview-library-icon');
  if (libIcon) {
    if (file.documentId && file.documentModalUrl) {
      const statusIcons = {
        'PENDING': 'bi-circle-fill text-secondary',
        'BLOCKED': 'bi-slash-circle text-warning',
        'INIT': 'bi-circle text-secondary',
        'PROCESSING': 'bi-arrow-repeat text-primary icn-spinner',
        'TEXT_EXTRACTED': 'bi-arrow-repeat text-primary icn-spinner',
        'PAUSED': 'bi-pause-circle-fill text-warning',
        'SUCCESS': 'bi-check-circle-fill text-success',
        'ERROR': 'bi-exclamation-circle-fill text-danger',
      };
      const badgeClass = statusIcons[file.documentStatus] || 'bi-circle-fill text-secondary';
      libIcon.innerHTML = `<span class="file-icon-wrapper" role="button"
        data-bs-toggle="modal" data-bs-target="#chat-next-modal"
        hx-get="${file.documentModalUrl}" hx-target="#chat-next-modal-content"
        hx-indicator="#librarian-modal-spinner" hx-swap="innerHTML" hx-trigger="click"
        onclick="event.stopPropagation();" title="View in library">
        <i class="bi bi-file-text text-muted"></i>
        <span class="status-badge"><i class="bi ${badgeClass}"></i></span>
      </span>`;
      htmx.process(libIcon);
      libIcon.style.display = '';
    } else {
      libIcon.innerHTML = '';
      libIcon.style.display = 'none';
    }
  }

  // Load preview content while preserving current content to avoid layout jumps.
  const contentEl = document.getElementById(`file-preview-content-${messageId}`);
  if (contentEl) {
    browser.dataset.previewExpectedUrl = file.previewUrl;
    contentEl.classList.add('is-loading');
    contentEl.setAttribute('aria-busy', 'true');
    htmx.ajax('GET', file.previewUrl, {target: contentEl, swap: 'innerHTML'});
  }
}

/**
 * Close the file preview and return to the file list view.
 * @param {string} messageId
 */
function closeFilePreview(messageId) {
  const browser = document.getElementById(`file-browser-${messageId}`);
  if (!browser) return;

  const listView = browser.querySelector('.file-list-view');
  const previewView = browser.querySelector('.file-preview-view');
  if (listView) listView.style.display = '';
  if (previewView) previewView.style.display = 'none';

  // Restore the message container width
  const stack = browser.closest('.message-stack');
  if (stack) stack.classList.remove('file-preview-active');
  scheduleChatLayoutPositionUpdate();

  // Resume file status polling immediately after closing preview
  const pollingContainer = browser.closest('[id^="message-files-"]');
  if (pollingContainer) {
    htmx.trigger(pollingContainer, 'file-status-refresh');
  }
}

/**
 * Navigate to the previous file in the preview.
 * @param {string} messageId
 */
function prevFilePreview(messageId) {
  const browser = document.getElementById(`file-browser-${messageId}`);
  if (!browser) return;
  const idx = parseInt(browser.dataset.currentIndex || '0', 10);
  if (idx > 0) openFilePreview(messageId, idx - 1);
}

/**
 * Navigate to the next file in the preview.
 * @param {string} messageId
 */
function nextFilePreview(messageId) {
  const browser = document.getElementById(`file-browser-${messageId}`);
  if (!browser) return;
  const files = _getFileData(messageId);
  const idx = parseInt(browser.dataset.currentIndex || '0', 10);
  if (idx < files.length - 1) openFilePreview(messageId, idx + 1);
}

/**
 * Initialize file preview content after HTMX swap.
 * Renders markdown via the same markdown-it pipeline as chat messages.
 * @param {HTMLElement} container - the preview-content element
 */
function initFilePreviewContent(container) {
  // Process HTMX attributes on newly swapped elements (e.g. librarian modal links)
  htmx.process(container);

  const mdEl = container.querySelector('.markdown-text');
  if (mdEl && mdEl.dataset.md) {
    let toParse = mdEl.dataset.md;
    try {
      toParse = JSON.parse(toParse);
    } catch (e) {
      toParse = false;
    }
    if (toParse) {
      mdEl.innerHTML = md.render(toParse).replaceAll('&lt;br&gt;', '<br>');
      // Add copy-code buttons to code blocks
      for (const block of mdEl.querySelectorAll('pre code')) {
        block.insertAdjacentHTML('beforebegin', copyCodeButtonHTML);
      }
    }
  }

  const previewInner = container.querySelector('.preview-content-inner');
  if (previewInner?.dataset.previewType === 'docx') {
    void initDocxPreviewContent(container);
  }

  updatePreviewToolbarActions(container);

  scheduleChatLayoutPositionUpdate();
}

function _markClicked(btn, duration = 2200) {
  btn.blur();
  btn.classList.add('clicked');
  setTimeout(() => btn.classList.remove('clicked'), duration);
}

function _getPreviewCopyButton(source) {
  if (source?.classList?.contains('preview-copy-btn')) {
    return source;
  }

  return source?.closest('.preview-copy-dropdown')?.querySelector('.preview-copy-btn') || null;
}

function _closePreviewCopyMenu(source) {
  const toggle = source?.closest('.preview-copy-dropdown')?.querySelector('.preview-copy-menu-toggle');
  if (!toggle || typeof bootstrap === 'undefined' || !bootstrap.Dropdown) {
    return;
  }

  bootstrap.Dropdown.getOrCreateInstance(toggle).hide();
}

function _stripCopiedPresentation(root) {
  if (!root) return;

  root.querySelectorAll('*').forEach(el => {
    el.style.removeProperty('background');
    el.style.removeProperty('background-color');
    el.style.removeProperty('font-family');
    el.style.removeProperty('color');
  });
}

function _getPreviewInnerFromButton(btn) {
  return btn.closest('.file-preview-view')?.querySelector('.preview-content-inner') || null;
}

function _getPreviewMarkdownSource(previewInner) {
  const markdownText = previewInner?.querySelector('.markdown-text');
  const markdownData = markdownText?.dataset?.md;
  if (!markdownData) return '';

  try {
    const parsed = JSON.parse(markdownData);
    return typeof parsed === 'string' ? parsed.trim() : '';
  } catch (_) {
    return String(markdownData).trim();
  }
}

function _isLiveDocxRenderTarget(container, renderToken) {
  const previewInner = container?.querySelector('.preview-content-inner[data-preview-type="docx"]');
  const wrapper = previewInner?.querySelector('.preview-docx-wrapper');
  return Boolean(
    container &&
    document.body.contains(container) &&
    wrapper &&
    wrapper.dataset.docxRenderToken === renderToken
  );
}

function _pickAlternateContentReplacement(alternateContent) {
  const branches = Array.from(alternateContent.children || []).filter(child =>
    child?.localName === 'Choice' || child?.localName === 'Fallback'
  );

  for (const branch of branches) {
    if (branch.firstElementChild) {
      return branch.firstElementChild.cloneNode(true);
    }
  }

  return null;
}

function _normalizeDocxXmlAlternateContent(xmlText) {
  if (!xmlText || !xmlText.includes('AlternateContent')) {
    return xmlText;
  }

  const parser = new DOMParser();
  const xmlDoc = parser.parseFromString(xmlText, 'application/xml');
  if (xmlDoc.querySelector('parsererror')) {
    return xmlText;
  }

  const alternates = Array.from(xmlDoc.getElementsByTagName('*')).filter(node => node.localName === 'AlternateContent');
  if (!alternates.length) {
    return xmlText;
  }

  let changed = false;
  for (const alternate of alternates) {
    const parent = alternate.parentNode;
    if (!parent) continue;

    const replacement = _pickAlternateContentReplacement(alternate);
    if (replacement) {
      parent.replaceChild(replacement, alternate);
    } else {
      parent.removeChild(alternate);
    }
    changed = true;
  }

  if (!changed) {
    return xmlText;
  }

  return new XMLSerializer().serializeToString(xmlDoc);
}

async function _normalizeDocxPackageForPreview(arrayBuffer) {
  if (!window.JSZip || typeof window.JSZip.loadAsync !== 'function') {
    return arrayBuffer;
  }

  const zip = await window.JSZip.loadAsync(arrayBuffer);
  const xmlEntries = Object.values(zip.files).filter(file => !file.dir && /^word\/.*\.xml$/i.test(file.name));
  let changed = false;

  for (const entry of xmlEntries) {
    const xmlText = await entry.async('string');
    const normalizedXml = _normalizeDocxXmlAlternateContent(xmlText);
    if (normalizedXml !== xmlText) {
      zip.file(entry.name, normalizedXml);
      changed = true;
    }
  }

  if (!changed) {
    return arrayBuffer;
  }

  return zip.generateAsync({type: 'arraybuffer'});
}

async function initDocxPreviewContent(container) {
  const previewInner = container.querySelector('.preview-content-inner[data-preview-type="docx"]');
  const wrapper = previewInner?.querySelector('.preview-docx-wrapper');
  const stage = wrapper?.querySelector('.preview-docx-stage');
  const status = wrapper?.querySelector('.preview-docx-status');
  const error = wrapper?.querySelector('.preview-docx-error');
  const docxUrl = wrapper?.dataset?.docxUrl;

  if (!previewInner || !wrapper || !stage || !docxUrl) return;

  previewInner.dataset.copyable = 'false';
  stage.innerHTML = '';
  error?.classList.add('d-none');
  status?.classList.remove('d-none');
  updatePreviewToolbarActions(container);

  if (!window.docx || typeof window.docx.renderAsync !== 'function') {
    status?.classList.add('d-none');
    error?.classList.remove('d-none');
    return;
  }

  const renderToken = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  wrapper.dataset.docxRenderToken = renderToken;

  try {
    const response = await fetch(docxUrl, {credentials: 'same-origin'});
    if (!response.ok) {
      throw new Error(`Failed to fetch DOCX preview (${response.status}).`);
    }

    const arrayBuffer = await response.arrayBuffer();
    const normalizedArrayBuffer = await _normalizeDocxPackageForPreview(arrayBuffer);
    if (!_isLiveDocxRenderTarget(container, renderToken)) return;

    await window.docx.renderAsync(normalizedArrayBuffer, stage, null, {
      className: 'preview-docx',
      inWrapper: true,
      hideWrapperOnPrint: true,
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
      renderEndnotes: true,
      useBase64URL: true,
    });

    if (!_isLiveDocxRenderTarget(container, renderToken)) return;

    status?.classList.add('d-none');
    error?.classList.add('d-none');
    previewInner.dataset.copyable = 'true';
    updatePreviewToolbarActions(container);
    scheduleChatLayoutPositionUpdate();
  } catch (renderError) {
    if (!_isLiveDocxRenderTarget(container, renderToken)) return;

    console.warn('Failed to render DOCX preview.', renderError);
    stage.innerHTML = '';
    previewInner.dataset.copyable = 'false';
    status?.classList.add('d-none');
    error?.classList.remove('d-none');
    updatePreviewToolbarActions(container);
  }
}

function updatePreviewToolbarActions(container) {
  const browser = container.closest('.file-browser');
  if (!browser) return;

  const copyDropdown = browser.querySelector('.preview-copy-dropdown');
  const copyBtn = copyDropdown?.querySelector('.preview-copy-btn');
  const copyMenuToggle = copyDropdown?.querySelector('.preview-copy-menu-toggle');
  if (!copyDropdown || !copyBtn) return;

  const previewInner = container.querySelector('.preview-content-inner');
  const copyable = previewInner?.dataset.copyable === 'true';
  const supportsMarkdownCopy = previewInner?.dataset.previewType === 'markdown';

  copyDropdown.classList.toggle('d-none', !copyable);
  copyBtn.classList.remove('d-none');
  copyBtn.disabled = !copyable;
  if (copyMenuToggle) {
    copyMenuToggle.classList.toggle('d-none', !supportsMarkdownCopy || !copyable);
    copyMenuToggle.disabled = !copyable;
  }
  if (!copyable) {
    copyBtn.classList.remove('clicked');
  }
}

async function _copyPreviewRichTextContent(previewInner) {
  const clone = previewInner.cloneNode(true);
  clone.querySelectorAll('.copy-message-button, .preview-copy-bar, .preview-html-source, .preview-docx-status, .preview-docx-error, script').forEach(el => el.remove());
  _stripCopiedPresentation(clone);
  await pasteRich(clone.innerHTML, clone.innerText);
}

async function _copyPreviewHtmlContent(previewInner) {
  const sourceTemplate = previewInner.querySelector('.preview-html-source');
  if (!sourceTemplate) {
    await _copyPreviewRichTextContent(previewInner);
    return;
  }

  const sourceHtml = sourceTemplate.content?.textContent || sourceTemplate.textContent || '';
  const tempDiv = document.createElement('div');
  tempDiv.innerHTML = sourceHtml;
  _stripCopiedPresentation(tempDiv);
  await pasteRich(tempDiv.innerHTML, tempDiv.innerText);
}

async function _copyPreviewImageContent(previewInner) {
  const img = previewInner.querySelector('.preview-image');
  if (!img) return;

  const imageSrc = img.currentSrc || img.getAttribute('src');
  const altText = img.getAttribute('alt') || '';

  if (imageSrc && window.ClipboardItem && navigator.clipboard?.write) {
    try {
      const response = await fetch(imageSrc, {credentials: 'same-origin'});
      if (response.ok) {
        const blob = await response.blob();
        if (blob.type && blob.type.startsWith('image/')) {
          const clipboardData = {[blob.type]: blob};
          if (altText) {
            clipboardData['text/plain'] = new Blob([altText], {type: 'text/plain'});
          }
          await navigator.clipboard.write([new ClipboardItem(clipboardData)]);
          return;
        }
      }
    } catch (error) {
      // Fall back to rich HTML copy below.
    }
  }

  const tempImg = document.createElement('img');
  tempImg.src = new URL(imageSrc, window.location.href).href;
  tempImg.alt = altText;
  await pasteRich(tempImg.outerHTML, altText || tempImg.src);
}

async function copyCurrentFilePreview(source, format = 'auto') {
  const btn = _getPreviewCopyButton(source);
  const previewInner = _getPreviewInnerFromButton(btn || source);
  if (!btn || !previewInner || previewInner.dataset.copyable !== 'true') return;

  const previewType = previewInner.dataset.previewType;
  btn.disabled = true;

  try {
    if (format === 'markdown' && previewType === 'markdown') {
      await navigator.clipboard.writeText(_getPreviewMarkdownSource(previewInner));
    } else if (previewType === 'html') {
      await _copyPreviewHtmlContent(previewInner);
    } else if (previewType === 'docx') {
      await _copyPreviewRichTextContent(previewInner);
    } else if (previewType === 'image') {
      await _copyPreviewImageContent(previewInner);
    } else {
      await _copyPreviewRichTextContent(previewInner);
    }
    _closePreviewCopyMenu(source);
    _markClicked(btn);
  } catch (error) {
    console.warn('Failed to copy preview content.', error);
  } finally {
    btn.disabled = false;
  }
}

function initializeChatLayoutPositionObservers() {
  if (!chatContainerEl) return;

  const messagesContainer = document.getElementById('messages-container');
  const observerTarget = messagesContainer || chatContainerEl;

  if (window.ResizeObserver) {
    const resizeObserver = new ResizeObserver(() => {
      scheduleChatLayoutPositionUpdate();
    });
    resizeObserver.observe(chatContainerEl);
    if (messagesContainer && messagesContainer !== chatContainerEl) {
      resizeObserver.observe(messagesContainer);
    }
  }

  if (window.MutationObserver && observerTarget) {
    const mutationObserver = new MutationObserver(() => {
      scheduleChatLayoutPositionUpdate();
    });
    mutationObserver.observe(observerTarget, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'style', 'open', 'aria-expanded']
    });
  }

  // Capture load events for newly inserted preview/content media that can alter layout.
  chatContainerEl.addEventListener('load', scheduleChatLayoutPositionUpdate, true);
}

/**
 * Copy the rendered rich text content of the preview to clipboard.
 * Uses the same pasteRich() function as copyMessage().
 */
async function copyPreviewRichText(btn) {
  const container = _getPreviewInnerFromButton(btn) || btn.closest('.preview-content-inner');
  if (!container) return;

  await _copyPreviewRichTextContent(container);
  _markClicked(btn);
}

/**
 * Copy the plain text content of the preview to clipboard.
 */
async function copyPreviewPlainText(btn) {
  const container = _getPreviewInnerFromButton(btn) || btn.closest('.preview-content-inner');
  if (!container) return;

  const pre = container.querySelector('pre code');
  if (pre) {
    await navigator.clipboard.writeText(pre.innerText);
  }

  _markClicked(btn);
}

// Listen for HTMX afterSwap on file preview content panels
document.addEventListener('htmx:afterSwap', function (e) {
  if (e.detail.target && e.detail.target.classList.contains('preview-content')) {
    e.detail.target.classList.remove('is-loading');
    e.detail.target.removeAttribute('aria-busy');
    initFilePreviewContent(e.detail.target);
  }
});

document.addEventListener('htmx:responseError', function (e) {
  const target = e.detail && e.detail.target;
  if (target && target.classList && target.classList.contains('preview-content')) {
    target.classList.remove('is-loading');
    target.removeAttribute('aria-busy');
  }
});

// For image previews, delay the DOM swap until the image is loaded.
// This keeps the previous preview visible and avoids flash on first image fetch.
document.addEventListener('htmx:beforeSwap', function (e) {
  const target = e.detail && e.detail.target;
  if (!target || !target.classList || !target.classList.contains('preview-content')) return;

  const xhr = e.detail && e.detail.xhr;
  if (!xhr || xhr.status < 200 || xhr.status >= 300) return;

  const responseHtml = xhr.responseText || '';
  if (!responseHtml.includes('preview-image')) return;

  const parser = new DOMParser();
  const doc = parser.parseFromString(responseHtml, 'text/html');
  const responseImg = doc.querySelector('.preview-image');
  const imgSrc = responseImg ? responseImg.getAttribute('src') : null;
  if (!imgSrc) return;

  const browser = target.closest('.file-browser');
  const expectedUrl = browser ? (browser.dataset.previewExpectedUrl || '') : '';
  let responsePath = '';
  try {
    responsePath = new URL(xhr.responseURL, window.location.origin).pathname;
  } catch (err) {
    responsePath = xhr.responseURL || '';
  }

  // If this response is stale due to rapid navigation, ignore it.
  if (expectedUrl && responsePath && !responsePath.endsWith(expectedUrl)) {
    e.detail.shouldSwap = false;
    return;
  }

  e.detail.shouldSwap = false;

  const applyImageSwap = () => {
    // Re-check staleness before applying late async swap.
    const latestExpectedUrl = browser ? (browser.dataset.previewExpectedUrl || '') : '';
    if (latestExpectedUrl && responsePath && !responsePath.endsWith(latestExpectedUrl)) {
      return;
    }

    target.innerHTML = responseHtml;
    target.classList.remove('is-loading');
    target.removeAttribute('aria-busy');
    initFilePreviewContent(target);
  };

  const preloader = new Image();
  preloader.src = imgSrc;

  if (preloader.complete) {
    requestAnimationFrame(applyImageSwap);
    return;
  }

  let settled = false;
  const settle = () => {
    if (settled) return;
    settled = true;
    requestAnimationFrame(applyImageSwap);
  };

  preloader.onload = settle;
  preloader.onerror = settle;
  setTimeout(settle, 5000);
});

// Defer library/task status polling swaps while a file preview is open.
// The outerHTML swap would destroy the preview state, so we cancel it and retry later.
document.addEventListener('htmx:beforeSwap', function (e) {
  const target = e.detail.target;
  if (!target || !target.id || !target.id.startsWith('message-files-')) return;

  const browser = target.querySelector('.file-browser');
  if (!browser) return;

  const previewView = browser.querySelector('.file-preview-view');
  if (previewView && previewView.style.display === 'block') {
    // Preview is open — cancel this swap but keep polling
    e.detail.shouldSwap = false;
    // Re-trigger polling with a custom event after a delay
    setTimeout(() => {htmx.trigger(target, 'file-status-refresh');}, 2000);
  }
});

window.downloadMessageFiles = downloadMessageFiles;
window.openFilePreview = openFilePreview;
window.closeFilePreview = closeFilePreview;
window.prevFilePreview = prevFilePreview;
window.nextFilePreview = nextFilePreview;
window.initFilePreviewContent = initFilePreviewContent;
window.copyCurrentFilePreview = copyCurrentFilePreview;
window.copyPreviewRichText = copyPreviewRichText;
window.copyPreviewPlainText = copyPreviewPlainText;

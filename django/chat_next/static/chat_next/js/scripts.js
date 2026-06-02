const CHAT_I18N = window.CHAT_NEXT_I18N || {};
function t(key, fallback) {
  return CHAT_I18N[key] ?? fallback;
}

const md = markdownit({
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre><code class="hljs">' +
          hljs.highlight(str, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (__) { }
    }

    return '<pre><code class="hljs">' + md.utils.escapeHtml(str) + '</code></pre>';
  },
  breaks: true,
  linkify: true,
});
md.use(katexPlugin);
md.renderer.rules.table_open = () => '<table class="table">\n';

function configureExternalLinkRendering(renderer) {
  const defaultLinkOpen = renderer.rules.link_open || function (tokens, idx, options, env, self) {
    return self.renderToken(tokens, idx, options);
  };

  renderer.rules.link_open = function (tokens, idx, options, env, self) {
    tokens[idx].attrSet('target', '_blank');
    tokens[idx].attrSet('rel', 'noopener noreferrer');
    return defaultLinkOpen(tokens, idx, options, env, self);
  };
}

configureExternalLinkRendering(md.renderer);

const SKILL_LINK_TOKEN_REGEX = /\[\[OPEN_SKILL:(\d+)\|([^\]]+)\]\]/g;

function openSkillEditorModal(skillId) {
  if (!/^\d+$/.test(String(skillId || ""))) return false;

  const skillsModal = document.getElementById("chat-next-modal");
  if (!skillsModal) return false;

  const modalBody = document.getElementById("chat-next-modal-content");
  if (modalBody) {
    const editUrl = `/chat_next/id/${chat_id}/skills/${skillId}/edit/`;
    htmx.ajax("GET", editUrl, {target: modalBody, swap: "innerHTML"});
  }

  const bsModal = bootstrap.Modal.getOrCreateInstance(skillsModal);
  bsModal.show();
  return true;
}

function closeNotificationsDropdown() {
  const toggle = document.getElementById('notifications-toggle');
  if (!toggle || typeof bootstrap === 'undefined' || !bootstrap.Dropdown) return;

  const dropdown = bootstrap.Dropdown.getOrCreateInstance(toggle);
  dropdown.hide();
}

function openChatNextModalUrl(url) {
  if (!url) return false;

  const modalEl = document.getElementById("chat-next-modal");
  const modalBody = document.getElementById("chat-next-modal-content");
  if (!modalEl || !modalBody) {
    window.location.href = url;
    return false;
  }

  htmx.ajax("GET", url, {target: modalBody, swap: "innerHTML"});
  bootstrap.Modal.getOrCreateInstance(modalEl).show();
  return true;
}

function getCurrentChatNextModalBackUrl() {
  return document.querySelector("#chat-next-modal-content > .chat-next-modal-view")?.dataset.modalCurrentUrl || "";
}

function buildContextHintLibrarianUrl(chatIdValue, hint, options = {}) {
  const chatIdParam = String(chatIdValue || "").trim();
  const hintType = String(hint?.type || "").trim();
  const hintId = String(hint?.id || "").trim();
  if (!chatIdParam || !hintId) return "";

  let path = "";
  if (hintType === "library") {
    path = `/chat_next/id/${chatIdParam}/modal/libraries/library/${hintId}/`;
  } else if (hintType === "folder") {
    path = `/chat_next/id/${chatIdParam}/modal/libraries/data_source/${hintId}/`;
  } else if (hintType === "document") {
    path = `/chat_next/id/${chatIdParam}/modal/libraries/document/${hintId}/`;
  } else if (hintType === "skill") {
    path = `/chat_next/id/${chatIdParam}/skills/${hintId}/edit/`;
  } else {
    return "";
  }

  const modalBackUrl = options.modalBackUrl ?? getCurrentChatNextModalBackUrl();
  if (!modalBackUrl) return path;

  const params = new URLSearchParams();
  params.set("modal_back_url", modalBackUrl);
  return `${path}?${params.toString()}`;
}

function openContextHintInLibrarian(hint, options = {}) {
  const chatIdValue = options.chatId || window.chat_id;
  const targetUrl = buildContextHintLibrarianUrl(chatIdValue, hint, options);
  if (!targetUrl) return false;
  return openChatNextModalUrl(targetUrl);
}

window.openChatNextModalUrl = openChatNextModalUrl;
window.getCurrentChatNextModalBackUrl = getCurrentChatNextModalBackUrl;
window.buildContextHintLibrarianUrl = buildContextHintLibrarianUrl;
window.openContextHintInLibrarian = openContextHintInLibrarian;

function extractSkillIdFromHref(hrefValue) {
  if (!hrefValue) return null;

  const href = String(hrefValue).trim();
  let m = href.match(/^skill:\/\/(\d+)$/i);
  if (m) return m[1];

  m = href.match(/^#skill-(\d+)$/i);
  if (m) return m[1];

  try {
    const absolute = new URL(href, window.location.origin);
    const byQuery = absolute.searchParams.get("open_skill");
    if (byQuery && /^\d+$/.test(byQuery)) {
      return byQuery;
    }
  } catch (_) {
    // Ignore malformed URLs
  }

  return null;
}

function normalizeSkillLinkTokens(markdownText) {
  if (!markdownText || typeof markdownText !== "string") return markdownText;
  return markdownText.replace(
    SKILL_LINK_TOKEN_REGEX,
    (_, skillId, label) => `[${label.trim()}](skill://${skillId})`
  );
}

function normalizeSkillLinksInCodeFormatting(markdownText) {
  if (!markdownText || typeof markdownText !== "string") return markdownText;

  let out = markdownText;

  // Inline code wrappers around skill links/tokens
  out = out.replace(/`(\[\[[\s\S]*?OPEN_SKILL:\d+\|[\s\S]*?\]\])`/g, "$1");
  out = out.replace(/`(\[[^\]]+\]\(skill:\/\/\d+\))`/g, "$1");

  // Fenced code blocks that only contain a single skill link/token line
  out = out.replace(
    /```(?:md|markdown|text)?\s*\n\s*(\[\[[\s\S]*?OPEN_SKILL:\d+\|[\s\S]*?\]\])\s*\n```/gi,
    "$1"
  );
  out = out.replace(
    /```(?:md|markdown|text)?\s*\n\s*(\[[^\]]+\]\(skill:\/\/\d+\))\s*\n```/gi,
    "$1"
  );

  return out;
}

function enhanceSkillActionLinks(container) {
  if (!container) return;

  container.querySelectorAll("a[href]").forEach((anchor) => {
    const skillId = extractSkillIdFromHref(anchor.getAttribute("href"));
    if (!skillId) return;

    const label = (anchor.textContent || "Open skill").trim() || "Open skill";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm btn-outline-primary ms-1";
    btn.setAttribute("data-skill-open-id", skillId);
    btn.innerHTML = `<i class="bi bi-lightbulb me-1"></i>${escapeHtml(label)}`;
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openSkillEditorModal(skillId);
    });

    anchor.replaceWith(btn);
  });
}

// Global timestamp for syncing typing animations across HTMX swaps
// The animation has a 1.5s duration, so we calculate where in the cycle we should be
const typingAnimationStart = performance.now();
const TYPING_ANIMATION_DURATION = 1500; // 1.5s in ms

// Bounded chat title polling: max 6 attempts (15 seconds total at 2.5s intervals)
// Prevents indefinite polling if workers are down or titles never resolve.
const TITLE_POLL_MAX_ATTEMPTS = 6;
const TITLE_POLL_INTERVAL_MS = 2500;
let chatTitlePollAttempts = 0;
let chatTitlePollTimer = null;

/**
 * Sync typing animation so dots don't reset on HTMX swaps.
 * Calculates negative animation-delay to maintain consistent animation phase.
 */
function syncTypingAnimation(typingElement) {
  if (!typingElement) return;
  const elapsed = performance.now() - typingAnimationStart;
  // Calculate the offset into the animation cycle
  const offset = elapsed % TYPING_ANIMATION_DURATION;
  const spans = typingElement.querySelectorAll('span');
  spans.forEach((span, index) => {
    // Each subsequent span has a 0.2s (200ms) delay offset
    const baseDelay = index * 200;
    // Apply negative delay to sync to global timeline, plus the natural stagger
    span.style.animationDelay = `${-offset + baseDelay}ms`;
  });
}

function collectPendingTitleChatIds() {
  return Array.from(document.querySelectorAll('#chat-history-list .chat-list-item[data-title-pending="true"]'))
    .map((el) => el.id?.replace('chat-list-item-', ''))
    .filter(Boolean);
}

function refreshPendingChatTitles() {
  const pendingIds = collectPendingTitleChatIds();

  if (!pendingIds.length || chatTitlePollAttempts >= TITLE_POLL_MAX_ATTEMPTS) {
    clearTimeout(chatTitlePollTimer);
    chatTitlePollTimer = null;
    chatTitlePollAttempts = 0;
    return;
  }

  chatTitlePollAttempts++;

  const currentChatId = document.getElementById('current-chat-hidden-field')?.value;
  if (!currentChatId) return;

  const params = new URLSearchParams();
  pendingIds.forEach((id) => params.append('chat_ids', id));
  const searchVal = document.getElementById('chat-history-search-input')?.value || '';
  if (searchVal) {
    params.set('search', searchVal);
  }

  const url = `/chat_next/id/${encodeURIComponent(currentChatId)}/refresh_titles/?${params.toString()}`;

  fetch(url)
    .then((res) => res.text())
    .then((html) => {
      if (!html.trim()) return;
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      let updatedCurrentTitle = false;
      tmp.querySelectorAll('[hx-swap-oob]').forEach((newEl) => {
        const existingEl = document.getElementById(newEl.id);
        if (existingEl) {
          if (newEl.querySelector('#current-chat-title')) updatedCurrentTitle = true;
          newEl.removeAttribute('hx-swap-oob');
          existingEl.outerHTML = newEl.outerHTML;
          const replacedEl = document.getElementById(newEl.id);
          if (replacedEl && typeof htmx !== 'undefined' && typeof htmx.process === 'function') {
            htmx.process(replacedEl);
          }
        }
      });
      if (updatedCurrentTitle) updatePageTitle();
    })
    .catch(() => { });

  chatTitlePollTimer = setTimeout(refreshPendingChatTitles, TITLE_POLL_INTERVAL_MS);
}

function schedulePendingTitleRefresh() {
  if (chatTitlePollTimer) {
    clearTimeout(chatTitlePollTimer);
  }

  chatTitlePollAttempts = 0;
  chatTitlePollTimer = setTimeout(refreshPendingChatTitles, TITLE_POLL_INTERVAL_MS);
}

const md_with_html = markdownit({
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre><code class="hljs">' +
          hljs.highlight(str, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (__) { }
    }

    return '<pre><code class="hljs">' + md.utils.escapeHtml(str) + '</code></pre>';
  },
  breaks: true,
  html: true,
  linkify: true,
});
md_with_html.use(katexPlugin);
md_with_html.renderer.rules.table_open = () => '<table class="table">\n';
configureExternalLinkRendering(md_with_html.renderer);

const MAX_MESSAGE_HEIGHT = 500;

function checkTruncation(element) {
  if (!element) return;

  const contentElement = element.querySelector('.markdown-text') || element;
  const messageOuter = element.closest('.message-outer');
  if (!messageOuter || !contentElement) return;

  if (contentElement.scrollHeight > MAX_MESSAGE_HEIGHT) {
    messageOuter.classList.add('truncate');
  } else {
    messageOuter.classList.remove('truncate');
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

const STREAMING_REASONING_CODE_PREVIEW_MAX_CHARS = 6000;
const STREAMING_REASONING_CODE_PREVIEW_MAX_LINES = 120;

function truncateStreamingCodePreview(code) {
  if (typeof code !== 'string' || !code) {
    return {code: code || '', truncated: false};
  }

  let preview = code;
  let truncated = false;
  const lines = preview.split('\n');

  if (lines.length > STREAMING_REASONING_CODE_PREVIEW_MAX_LINES) {
    preview = lines.slice(0, STREAMING_REASONING_CODE_PREVIEW_MAX_LINES).join('\n');
    truncated = true;
  }

  if (preview.length > STREAMING_REASONING_CODE_PREVIEW_MAX_CHARS) {
    preview = preview.slice(0, STREAMING_REASONING_CODE_PREVIEW_MAX_CHARS);
    truncated = true;
  }

  if (truncated) {
    preview = preview.replace(/\s+$/, '') + '\n…';
  }

  return {code: preview, truncated};
}

function renderEscapedCodeBlock(code, className = 'hljs') {
  return '<pre><code class="' + className + '">' + escapeHtml(code) + '</code></pre>';
}

function findNextFencedCodeBlock(text, startIndex = 0) {
  const openingFenceRegex = /(^|\n)(`{3,})([^\n`]*)[ \t]*\n/g;
  openingFenceRegex.lastIndex = startIndex;

  const openingMatch = openingFenceRegex.exec(text);
  if (!openingMatch) {
    return null;
  }

  const prefix = openingMatch[1] || '';
  const fence = openingMatch[2] || '```';
  const infoString = (openingMatch[3] || '').trim();
  const contentStart = openingFenceRegex.lastIndex;
  const closingFenceRegex = new RegExp(`\\n${fence}[ \\t]*(?=\\n|$)`, 'g');
  closingFenceRegex.lastIndex = contentStart;

  const closingMatch = closingFenceRegex.exec(text);
  if (!closingMatch) {
    return null;
  }

  return {
    startIndex: openingMatch.index + prefix.length,
    endIndex: closingMatch.index + closingMatch[0].length,
    infoString,
    code: text.slice(contentStart, closingMatch.index),
  };
}

/**
 * Render markdown code blocks in text, but escape everything else for safety.
 * This is used for processing step details that may contain code blocks.
 */
function renderCodeBlocks(text, options = {}) {
  if (!text) return '';

  const disableSyntaxHighlight = options.disableSyntaxHighlight === true;
  const truncateStreamingCode = options.truncateStreamingCode === true;
  const truncatedStreamingNotice = options.truncatedStreamingNotice
    || t(
      'streaming_processing_steps_code_trimmed',
      'Large code preview trimmed during streaming to keep the browser responsive. Full code will appear when the response finishes.'
    );

  const firstCodeBlock = findNextFencedCodeBlock(text);
  if (!firstCodeBlock) {
    // No code blocks, just escape the text
    return escapeHtml(text);
  }

  // Replace code blocks with highlighted HTML, escape everything else
  let result = '';
  let lastIndex = 0;
  let block = firstCodeBlock;

  while (block) {
    // Escape text before this code block
    if (block.startIndex > lastIndex) {
      result += escapeHtml(text.slice(lastIndex, block.startIndex));
    }

    // Render the code block with syntax highlighting
    const lang = (block.infoString.split(/\s+/, 1)[0] || 'plaintext').trim();
    const code = block.code;
    const preview = truncateStreamingCode ? truncateStreamingCodePreview(code) : {code, truncated: false};
    const codeToRender = preview.code;

    if (!disableSyntaxHighlight && lang && hljs.getLanguage(lang)) {
      try {
        result += '<pre><code class="hljs">' +
          hljs.highlight(codeToRender, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (e) {
        result += renderEscapedCodeBlock(codeToRender);
      }
    } else {
      result += renderEscapedCodeBlock(codeToRender);
    }

    if (preview.truncated) {
      result += `<div class="reasoning-step-streaming-note text-muted small fst-italic mt-2">${escapeHtml(truncatedStreamingNotice)}</div>`;
    }

    lastIndex = block.endIndex;
    block = findNextFencedCodeBlock(text, lastIndex);
  }

  // Escape any remaining text after the last code block
  if (lastIndex < text.length) {
    result += escapeHtml(text.slice(lastIndex));
  }

  return result;
}

function render_markdown(element) {
  // Render reasoning summary first
  if (typeof render_reasoning === "function") {
    render_reasoning(element);
  }

  // Render markdown in the element
  const markdown_text = element.querySelector(".markdown-text");
  dot_element = element.querySelector(".typing"); // Exists when dots=True on htmx_stream call

  // Sync typing animation to global timeline so dots don't reset on HTMX swaps
  if (dot_element) {
    syncTypingAnimation(dot_element);
  }

  // Check if we're in a streaming response (not yet finalized)
  const isStreaming = element.closest('.chat-streaming-response') !== null ||
    element.id?.startsWith('response-');

  if (markdown_text) {
    let to_parse = markdown_text.dataset.md;
    try {
      to_parse = JSON.parse(to_parse);
    } catch (e) {
      to_parse = false;
    }
    if (typeof to_parse === "string") {
      to_parse = normalizeSkillLinksInCodeFormatting(to_parse);
      to_parse = normalizeSkillLinkTokens(to_parse);
    }
    const parent = markdown_text.parentElement;
    if (to_parse) {
      // IMPORTANT: Only replace markdown_text content, not the entire parent
      // This preserves the reasoning-summary sibling element
      markdown_text.innerHTML = md.render(to_parse).replaceAll('&lt;br&gt;', '<br>');
      enhanceSkillActionLinks(markdown_text);
      const current_dots = parent.parentElement.querySelector(".typing");
      // If dots=True on htmx_stream call and we just removed the dots at the beginning of stream,
      // add a new dots element after parent
      if (dot_element && !current_dots) {
        parent.insertAdjacentHTML("afterend", "\n\n" + dot_element.outerHTML);
        // Sync the newly added typing animation
        const newDots = parent.parentElement.querySelector(".typing");
        syncTypingAnimation(newDots);
      }
      if (current_dots && !dot_element) {
        current_dots.remove();
      }
      // Add the "copy code" button to code blocks
      for (block of markdown_text.querySelectorAll("pre code")) {
        block.insertAdjacentHTML("beforebegin", copyCodeButtonHTML);
      }
      // Handle sandbox: URLs during streaming - mark images and links as pending
      if (isStreaming) {
        // Get translated strings from the streaming response element
        // element should be the streaming container or its child
        const streamingEl = element.closest('.chat-streaming-response') || element;
        const imageLoadingText = streamingEl?.dataset?.imageLoadingText || t('image_loading', 'Image loading...');
        const filePendingText = streamingEl?.dataset?.filePendingText || t('file_pending', 'File will be available when response completes');
        handlePendingSandboxUrls(markdown_text, imageLoadingText, filePendingText);
      }
    } else if ((after_text = parent.nextElementSibling)) {
      // If stream is empty (which should only happen between batches), it will stream dots
      // so we can remove the dot element we manually added above
      if (after_text.classList.contains("typing")) {
        after_text.remove();
      }
    }
  }
}

/**
 * Handle sandbox: URLs during streaming by marking them as pending.
 * Images get hidden and replaced with placeholders.
 * File links are replaced with visible non-link pending chips until hydration.
 * @param {Element} container - The markdown container element
 * @param {string} imageLoadingText - Translated text for image loading placeholder
 * @param {string} filePendingText - Translated text for file pending helper text
 */
function handlePendingSandboxUrls(container, imageLoadingText, filePendingText) {
  // Find and handle sandbox images
  const images = container.querySelectorAll('img[src^="sandbox:"]');
  images.forEach(img => {
    // Skip if already processed
    if (img.classList.contains('sandbox-pending')) return;

    img.classList.add('sandbox-pending');

    // Create placeholder element if not already present
    if (!img.nextElementSibling?.classList.contains('sandbox-image-placeholder')) {
      const placeholder = document.createElement('div');
      placeholder.className = 'sandbox-image-placeholder';
      placeholder.innerHTML = `<i class="bi bi-image"></i> <span>${imageLoadingText}</span>`;
      img.insertAdjacentElement('afterend', placeholder);
    }
  });

  // Find and handle sandbox links
  const links = container.querySelectorAll('a[href^="sandbox:"]');
  links.forEach(link => {
    const href = link.getAttribute('href') || '';
    let label = (link.textContent || '').trim();

    if (!label || label === href) {
      const fallbackPath = href.replace(/^sandbox:/i, '').trim();
      const fallbackName = fallbackPath.split('/').filter(Boolean).pop() || fallbackPath || filePendingText;
      try {
        label = decodeURIComponent(fallbackName);
      } catch (_) {
        label = fallbackName;
      }
    }

    const pendingChip = document.createElement('span');
    pendingChip.className = 'sandbox-file-pending';
    pendingChip.setAttribute('role', 'status');
    pendingChip.setAttribute('aria-label', `${label}. ${filePendingText}`);
    pendingChip.innerHTML = [
      '<i class="bi bi-hourglass-split" aria-hidden="true"></i>',
      `<span class="sandbox-file-pending-label">${escapeHtml(label)}</span>`,
      `<span class="sandbox-file-pending-note">${escapeHtml(filePendingText)}</span>`
    ].join('');

    link.replaceWith(pendingChip);
  });
}

// Chat window UI
const chatContainerEl = document.querySelector("#chat-container");
const chatContentContainerEl = document.querySelector("#chat-content-container");
let chatLayoutPositionRaf = null;
const SIDEBAR_COLLAPSE_BREAKPOINT_PX = 1023;
const CHAT_BOTTOM_THRESHOLD_PX = 30;
const chatScrollState = {
  autoScrollEnabled: true,
  programmaticScrollDepth: 0,
  pendingPinRaf: null,
  resizeObserver: null,
};

function getChatScrollContainer() {
  return chatContainerEl;
}

function getChatDistanceFromBottom(container = getChatScrollContainer()) {
  if (!container) return Infinity;

  return Math.max(0, container.scrollHeight - container.scrollTop - container.clientHeight);
}

function isChatNearBottom(container = getChatScrollContainer()) {
  return getChatDistanceFromBottom(container) <= CHAT_BOTTOM_THRESHOLD_PX;
}

function updateChatAutoScrollPreference() {
  const container = getChatScrollContainer();
  if (!container) return false;

  chatScrollState.autoScrollEnabled = isChatNearBottom(container);
  return chatScrollState.autoScrollEnabled;
}

function runWithProgrammaticChatScroll(callback) {
  chatScrollState.programmaticScrollDepth += 1;

  try {
    callback();
  } finally {
    requestAnimationFrame(() => {
      chatScrollState.programmaticScrollDepth = Math.max(0, chatScrollState.programmaticScrollDepth - 1);
    });
  }
}

function pinChatToBottom(force = false) {
  const container = getChatScrollContainer();
  if (!container) return false;

  if (force) {
    chatScrollState.autoScrollEnabled = true;
  }

  if (!force && !chatScrollState.autoScrollEnabled) {
    return false;
  }

  runWithProgrammaticChatScroll(() => {
    container.scrollTop = container.scrollHeight;
  });

  return true;
}

function pinChatToBottomImmediately(force = false) {
  const container = getChatScrollContainer();
  if (!container) return false;

  if (force) {
    chatScrollState.autoScrollEnabled = true;
  }

  if (!force && !chatScrollState.autoScrollEnabled) {
    return false;
  }

  container.scrollTop = container.scrollHeight;
  return true;
}

function requestChatNextScrollToBottom(force = false) {
  if (!force && !chatScrollState.autoScrollEnabled) {
    return;
  }

  // Pin immediately so rapid streaming markdown re-renders do not outrun a
  // scroll request that is waiting for the next frame.
  pinChatToBottomImmediately(force);

  if (chatScrollState.pendingPinRaf) {
    cancelAnimationFrame(chatScrollState.pendingPinRaf);
  }

  chatScrollState.pendingPinRaf = requestAnimationFrame(() => {
    chatScrollState.pendingPinRaf = null;
    pinChatToBottom(force);
  });
}

function pinCompletedStreamToBottom() {
  requestChatNextScrollToBottom(false);
}

function shouldChatAutoScroll() {
  return chatScrollState.autoScrollEnabled;
}

function shouldPinChatToBottomOnInitialLoad() {
  if (!chatContainerEl) {
    return false;
  }

  if (document.querySelector("#no-messages-placeholder") !== null) {
    return false;
  }

  return !(window.location.hash || "").startsWith("#message_");
}

function initializeChatAutoScrollObservers() {
  if (!chatContentContainerEl || typeof ResizeObserver === 'undefined' || chatScrollState.resizeObserver) {
    return;
  }

  chatScrollState.resizeObserver = new ResizeObserver(() => {
    requestChatNextScrollToBottom();
  });
  chatScrollState.resizeObserver.observe(chatContentContainerEl);
}

if (chatContainerEl) {
  chatContainerEl.addEventListener('scroll', function () {
    if (chatScrollState.programmaticScrollDepth > 0) {
      return;
    }

    updateChatAutoScrollPreference();
  }, {passive: true});
}

window.requestChatNextScrollToBottom = requestChatNextScrollToBottom;
window.chatNextShouldAutoScroll = shouldChatAutoScroll;
window.runWithProgrammaticChatNextScroll = runWithProgrammaticChatScroll;

function isLeftSidebarOverlayMode() {
  return window.innerWidth <= SIDEBAR_COLLAPSE_BREAKPOINT_PX;
}

function syncLeftSidebarBackdrop() {
  const leftSidebar = document.querySelector("#left-sidebar");
  const backdrop = document.querySelector("#left-sidebar-backdrop");
  if (!backdrop) {
    return;
  }

  const showBackdrop = !!leftSidebar
    && isLeftSidebarOverlayMode()
    && !leftSidebar.classList.contains("collapsed")
    && !leftSidebar.classList.contains("hidden");

  backdrop.classList.toggle("visible", showBackdrop);
}
function scheduleChatLayoutPositionUpdate() {
  if (!chatContainerEl) return;
  if (chatLayoutPositionRaf) return;
  chatLayoutPositionRaf = requestAnimationFrame(() => {
    chatLayoutPositionRaf = null;
    resizeOtherElements();
    if (shouldChatAutoScroll()) {
      requestChatNextScrollToBottom();
    }
  });
}
window.addEventListener('resize', scheduleChatLayoutPositionUpdate);

const copyCodeButtonHTML = `<button type="button" onclick="copyCode(this)"
class="btn btn-link m-0 p-0 text-muted copy-message-button copy-button"
title="${t('copy', 'Copy')}"><i class="bi bi-copy"></i><i class="bi bi-check-lg"></i></button>`;

function scrollToListItem() {
  setTimeout(() => {
    const historyList = document.getElementById('chat-history-list');
    if (!historyList) return;

    const scrollContainer = document.getElementById('left-sidebar-scroll') || historyList;

    const currentChat = historyList.querySelector('.chat-list-item.current');
    if (!currentChat) return;

    // IMPORTANT: only scroll the sidebar container.
    // Using element.scrollIntoView can scroll outer ancestors (including page)
    // which can cause layout jump in medium breakpoints.
    const stickyHeader = document.getElementById('new-chat-button-outer');
    const stickyOffset = stickyHeader ? stickyHeader.offsetHeight : 0;

    const itemTop = currentChat.offsetTop;
    const itemHeight = currentChat.offsetHeight;
    const itemBottom = itemTop + itemHeight;
    const scrollTop = scrollContainer.scrollTop;
    const containerHeight = scrollContainer.clientHeight;
    const visibleBottom = scrollTop + containerHeight;

    // Only scroll if the item is outside the visible bounds
    if (itemTop < scrollTop + stickyOffset) {
      // Item is above visible area (or hidden by sticky header), scroll to top
      scrollContainer.scrollTo({
        top: Math.max(0, itemTop - stickyOffset),
        behavior: 'auto'
      });
    } else if (itemBottom > visibleBottom) {
      // Item is below visible area, scroll to bottom
      scrollContainer.scrollTo({
        top: Math.max(0, itemBottom - containerHeight),
        behavior: 'auto'
      });
    }
    // If item is already visible, do nothing
  }, 100);
}


function confirmRerun(triggerEl, evt) {
  const messageOuter = triggerEl.closest('.message-outer');
  const messageId = messageOuter.id.substring('message_'.length);

  const messages = Array.from(document.querySelectorAll('#messages-container .message-outer'));
  const lastUserMsg = [...messages].reverse().find(el => (el.dataset.isBot || '').toLowerCase() === 'false');
  const lastUserMsgId = lastUserMsg ? lastUserMsg.id.substring('message_'.length) : null;

  // if the user is rerunning their last message, no need to confirm
  if (!lastUserMsg || messageId === lastUserMsgId) {
    return;
  }

  const confirmMessage = triggerEl.dataset.confirmText;
  if (!window.confirm(confirmMessage)) {
    evt.preventDefault();
    evt.stopImmediatePropagation();
  }
}

const pendingInlineEditDimensions = new Map();
const INLINE_EDIT_MIN_WIDTH_PX = 240;

function autoResizeInlineEditTextarea(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = textarea.scrollHeight + 'px';
}

function preserveInlineEditMessageDimensions(triggerEl) {
  const messageOuter = triggerEl?.closest('.message-outer');
  const messageBlob = messageOuter?.querySelector('.message-blob');

  if (!messageOuter?.id || !messageBlob) {
    return;
  }

  pendingInlineEditDimensions.set(messageOuter.id, {
    width: messageBlob.offsetWidth,
    height: messageBlob.offsetHeight,
  });
}

function focusInlineEditTextarea(textarea) {
  if (!textarea) {
    return;
  }

  textarea.focus();

  const textLength = textarea.value.length;
  if (typeof textarea.setSelectionRange === 'function') {
    textarea.setSelectionRange(textLength, textLength);
  }
}

function getInlineEditMinimumWidth(messageOuter, maxAvailableWidth) {
  const widthCandidates = [Math.min(maxAvailableWidth, INLINE_EDIT_MIN_WIDTH_PX)];
  const actionRow = messageOuter?.querySelector('.message-inline-edit-actions');

  if (actionRow) {
    widthCandidates.push(Math.min(maxAvailableWidth, actionRow.scrollWidth));
  }

  return Math.max(...widthCandidates);
}

function applyInlineEditMessageDimensions(messageOuter) {
  if (!messageOuter?.id) {
    return;
  }

  const textarea = messageOuter.querySelector('.message-inline-edit-textarea');
  const savedDimensions = pendingInlineEditDimensions.get(messageOuter.id);
  if (!savedDimensions) {
    if (textarea) {
      autoResizeInlineEditTextarea(textarea);
    }
    focusInlineEditTextarea(textarea);
    return;
  }

  const messageBlob = messageOuter.querySelector('.message-blob');
  const messageStack = messageOuter.querySelector('.message-stack');
  const maxAvailableWidth = messageStack?.parentElement?.clientWidth || savedDimensions.width;
  const minimumInlineEditWidth = getInlineEditMinimumWidth(messageOuter, maxAvailableWidth);
  const targetWidth = Math.min(
    Math.max(savedDimensions.width, minimumInlineEditWidth),
    maxAvailableWidth,
  );

  if (messageBlob) {
    messageBlob.style.boxSizing = 'border-box';
    messageBlob.style.width = `${targetWidth}px`;
    messageBlob.style.minHeight = `${savedDimensions.height}px`;
  }

  if (textarea) {
    autoResizeInlineEditTextarea(textarea);
  }

  focusInlineEditTextarea(textarea);
  pendingInlineEditDimensions.delete(messageOuter.id);
}

function updateRerunPromptButtons() {
  const mode = document.querySelector('#chat-outer')?.classList[0];
  const allowFileRerun = mode === 'summarize' || mode === 'translate';
  document.querySelectorAll(".rerun-prompt-button").forEach((button) => {
    const messageOuter = button.closest('.message-outer');
    const hasFiles = messageOuter?.dataset.hasFiles === 'true';
    const shouldHide = hasFiles && !allowFileRerun;
    button.classList.toggle("d-none", shouldHide);
  });
}


// Close the sidebars that are in "overlay mode" when clicking outside of them
document.querySelector("#chat-container").addEventListener('click', function (e) {
  let clicked_element = e.target;
  let left_sidebar = document.querySelector('#left-sidebar');
  if (!left_sidebar) {
    return;
  }
  if (!(clicked_element.closest(".chat-sidebar-toggle") || left_sidebar.contains(clicked_element))) {
    if (window.getComputedStyle(left_sidebar).position === "absolute" && !left_sidebar.classList.contains("hidden")) {
      closeSidebar("left-sidebar");
    }
  }
});

document.querySelector("#left-sidebar-backdrop")?.addEventListener("click", function () {
  closeSidebar("left-sidebar");
});

// Some resizing hacks to make the prompt form the same width as the messages
function resizePromptContainer() {
  let chatContainer = document.querySelector("#chat-container");
  let chatContentContainer = document.querySelector("#chat-content-container");
  let promptContainer = document.querySelector('#prompt-form-container');
  if (!chatContainer || !promptContainer) return;
  // Align to the main chat content container (same layout layer as the prompt's inner container).
  let targetRect = (chatContentContainer || chatContainer).getBoundingClientRect();
  promptContainer.style.left = targetRect.left + "px";
  promptContainer.style.right = "auto";
  promptContainer.style.width = targetRect.width + "px";
  promptContainer.style.visibility = "visible";
  resizeOtherElements();
}
function showHideSidebars() {
  if (window.innerWidth <= SIDEBAR_COLLAPSE_BREAKPOINT_PX) {
    closeSidebar("left-sidebar", false);
  } else {
    openSidebar("left-sidebar", false);
  }
  resizePromptContainer();
}
window.addEventListener('resize', showHideSidebars);
// Initialize or re-initialize bootstrap tooltips
function initializeTooltips(root = document) {
  if (typeof bootstrap === 'undefined' || !bootstrap.Tooltip || !root) return;

  const tooltipTriggerList = [];
  if (typeof root.matches === 'function' && root.matches('[data-bs-toggle="tooltip"]')) {
    tooltipTriggerList.push(root);
  }
  if (typeof root.querySelectorAll === 'function') {
    tooltipTriggerList.push(...root.querySelectorAll('[data-bs-toggle="tooltip"]'));
  }

  tooltipTriggerList.forEach((tooltipTriggerEl) => {
    bootstrap.Tooltip.getOrCreateInstance(tooltipTriggerEl, {
      delay: {show: 500, hide: 200}
    });
  });
}

function shouldPreserveToolbarControlFocus(target) {
  if (!target) return false;

  return !!target.closest(
    'button, a, input, textarea, select, label, .dropdown-menu, .context-picker, .chat-model-selector'
  );
}

// On page load...
document.addEventListener("DOMContentLoaded", function () {
  showHideSidebars();

  // Markdown rendering
  document.querySelectorAll("div.message-text").forEach(function (element) {
    render_markdown(element);
    checkTruncation(element);
  });
  updateRerunPromptButtons();
  if (typeof moveChatPromptCaretToEnd === "function") {
    moveChatPromptCaretToEnd();
  }
  // Set up prompt/toolbar sizing without causing CLS
  resizeOtherElements();
  if (shouldPinChatToBottomOnInitialLoad()) {
    requestChatNextScrollToBottom(true);
  }
  updateChatAutoScrollPreference();
  initializeChatAutoScrollObservers();
  // Only resize textarea if it has pre-filled content to avoid CLS
  const chatPrompt = document.querySelector('#chat-prompt');
  if (chatPrompt && chatPrompt.value.trim().length > 0) {
    resizeTextarea();
  }
  if (chatPrompt) {
    chatPrompt.focus();
  }
  if (typeof initializeChatLayoutPositionObservers === "function") {
    initializeChatLayoutPositionObservers();
  }
  // Initialize tooltips
  initializeTooltips();
  schedulePendingTitleRefresh();
  const chatToolbar = document.querySelector("#chat-toolbar");
  if (chatToolbar) {
    chatToolbar.addEventListener("click", function (event) {
      if (shouldPreserveToolbarControlFocus(event.target)) {
        return;
      }
      document.querySelector("#chat-prompt")?.focus();
    });
  }

  // Open a skill editor when URL specifies one.
  // Supports query param (?open_skill=123) and hash (#skill-123).
  try {
    const params = new URLSearchParams(window.location.search);
    const querySkillId = params.get("open_skill");
    if (querySkillId && /^\d+$/.test(querySkillId)) {
      if (openSkillEditorModal(querySkillId)) {
        params.delete("open_skill");
        const nextQuery = params.toString();
        history.replaceState(
          null,
          "",
          window.location.pathname + (nextQuery ? `?${nextQuery}` : "") + window.location.hash
        );
      }
    } else {
      const skillMatch = window.location.hash.match(/^#skill-(\d+)$/);
      if (skillMatch && openSkillEditorModal(skillMatch[1])) {
        // Clear hash so refreshing doesn't re-open
        history.replaceState(null, "", window.location.pathname + window.location.search);
      }
    }
  } catch (_) { /* noop */}
});

function pruneEmptyClipboardNode(node, boundaryRoot) {
  while (node && node !== boundaryRoot && node.nodeType === Node.ELEMENT_NODE && node.childNodes.length === 0) {
    const parent = node.parentNode;
    node.remove();
    node = parent;
  }
}

function trimClipboardBoundaryWhitespace(container) {
  if (!container) return;

  const trimBoundary = (trimStart) => {
    while (true) {
      const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
      let textNode = null;
      let currentNode = null;

      if (trimStart) {
        textNode = walker.nextNode();
      } else {
        while ((currentNode = walker.nextNode())) {
          textNode = currentNode;
        }
      }

      if (!textNode) {
        return;
      }

      const originalText = textNode.textContent || "";
      const trimmedText = trimStart
        ? originalText.replace(/^\s+/, "")
        : originalText.replace(/\s+$/, "");

      if (trimmedText.length > 0) {
        textNode.textContent = trimmedText;
        return;
      }

      const parent = textNode.parentNode;
      textNode.remove();
      pruneEmptyClipboardNode(parent, container);
    }
  };

  trimBoundary(true);
  trimBoundary(false);
}

function getTrimmedClipboardPayload(container) {
  if (!container) {
    return {html: "", plain: ""};
  }

  trimClipboardBoundaryWhitespace(container);

  return {
    html: container.innerHTML.trim(),
    plain: container.innerText.trim(),
  };
}

// Intercept native copy (Ctrl+C) to strip background colors and fonts from copied text
document.addEventListener("copy", function (event) {
  const selection = window.getSelection();
  if (!selection.rangeCount) return;

  // Check if the selection is within a message area
  const range = selection.getRangeAt(0);
  const container = range.commonAncestorContainer;
  const messageOuter = container.nodeType === Node.ELEMENT_NODE
    ? container.closest(".message-outer")
    : container.parentElement?.closest(".message-outer");

  if (!messageOuter) return; // Not in a message, let default copy happen

  // Clone the selected content
  const clonedContent = range.cloneContents();
  const tempDiv = document.createElement("div");
  tempDiv.appendChild(clonedContent);

  // Strip background colors and font information from all elements
  tempDiv.querySelectorAll("*").forEach(el => {
    el.style.removeProperty("background");
    el.style.removeProperty("background-color");
    el.style.removeProperty("font-family");
    el.style.removeProperty("color");
  });

  const clipboardPayload = getTrimmedClipboardPayload(tempDiv);

  // Set the clipboard data with cleaned HTML and plain text
  event.clipboardData.setData("text/html", clipboardPayload.html);
  event.clipboardData.setData("text/plain", clipboardPayload.plain);
  event.preventDefault();
});

let skillsModalScrollState = null;

function captureSkillsModalScrollState() {
  const modalBody = document.getElementById("chat-next-modal-content");
  if (!modalBody) return null;
  return {
    bodyScrollTop: modalBody.scrollTop,
    columnScrollTops: Array.from(modalBody.querySelectorAll(".scroll-col")).map((el) => el.scrollTop),
    leftColScrollTop: modalBody.querySelector("#skills-card-list .col-3")?.scrollTop || 0,
    rightColScrollTop: modalBody.querySelector("#skills-card-list .col-9")?.scrollTop || 0,
  };
}

function restoreSkillsModalScrollState(state) {
  if (!state) return;
  const modalBody = document.getElementById("chat-next-modal-content");
  if (!modalBody) return;

  modalBody.scrollTop = state.bodyScrollTop || 0;

  const cols = Array.from(modalBody.querySelectorAll(".scroll-col"));
  cols.forEach((el, idx) => {
    if (idx < state.columnScrollTops.length) {
      el.scrollTop = state.columnScrollTops[idx] || 0;
    }
  });

  const leftCol = modalBody.querySelector("#skills-card-list .col-3");
  if (leftCol) leftCol.scrollTop = state.leftColScrollTop || 0;
  const rightCol = modalBody.querySelector("#skills-card-list .col-9");
  if (rightCol) rightCol.scrollTop = state.rightColScrollTop || 0;
}

document.addEventListener("htmx:beforeRequest", function (event) {
  const requestElement = event.detail?.elt || event.target;
  if (requestElement?.classList?.contains('edit-message-button')) {
    preserveInlineEditMessageDimensions(requestElement);
  }

  const target = event.detail?.target;
  if (target?.id === "chat-next-modal-content") {
    skillsModalScrollState = captureSkillsModalScrollState();
  }
});

document.addEventListener("htmx:afterSwap", function (event) {
  const target = event.detail?.target;
  if (target?.id === "chat-next-modal-content" && skillsModalScrollState) {
    const state = skillsModalScrollState;
    requestAnimationFrame(() => {
      restoreSkillsModalScrollState(state);
      skillsModalScrollState = null;
    });
  }
});

document.addEventListener("click", function (event) {
  if (event.target.closest(".context-pill-remove")) return;

  const pill = event.target.closest(".context-pill-openable[data-open-url]");
  if (!pill) return;

  event.preventDefault();
  event.stopPropagation();
  openChatNextModalUrl(pill.dataset.openUrl);
});

document.addEventListener("keydown", function (event) {
  if (!["Enter", " ", "Spacebar"].includes(event.key)) return;

  const pill = event.target.closest(".context-pill-openable[data-open-url]");
  if (!pill) return;

  event.preventDefault();
  event.stopPropagation();
  openChatNextModalUrl(pill.dataset.openUrl);
});

document.addEventListener('click', function (event) {
  const anchor = event.target.closest('#notifications-list a[href]');
  if (!anchor) return;

  const skillId = extractSkillIdFromHref(anchor.getAttribute('href'));
  if (!skillId) return;

  if (openSkillEditorModal(skillId)) {
    event.preventDefault();
    event.stopPropagation();
    closeNotificationsDropdown();
  }
});

// On prompt form submit...
document.addEventListener("htmx:afterSwap", function (event) {
  if (event.detail?.target?.id != "messages-container") return;
  if (document.querySelector("#no-messages-placeholder") !== null) {
    document.querySelector("#no-messages-placeholder").remove();
  }
  // Check truncation
  document.querySelectorAll("div.message-text").forEach(function (element) {
    checkTruncation(element);
  });
  // Markdown rendering, if the response message has data-md property (e.g., error message)
  let messages = document.querySelectorAll("#messages-container div.markdown-text");
  let last_message = messages[messages.length - 1];
  if (last_message && last_message.dataset.md) {
    render_markdown(last_message.parentElement);
  }
  const requestElement = event.detail?.requestConfig?.elt;
  const isPromptMessageSubmit = requestElement?.id === "prompt-form"
    || requestElement?.id === "send-button"
    || !!requestElement?.closest?.("#prompt-form");

  if (isPromptMessageSubmit) {
    document.querySelector("#chat-prompt").value = "";
    // Clear the prompt upload area after form submission
    if (typeof clearPromptUploadedFiles === 'function') {
      clearPromptUploadedFiles();
    }
    if (!chat_tour_in_progress) {
      document.querySelector("#chat-prompt").focus();
    }
    // Change height back to minimum
    const minHeight = typeof getChatPromptMinHeight === 'function' ? getChatPromptMinHeight() : 85;
    document.querySelector("#chat-prompt").style.height = minHeight + "px";
    lastHeight = minHeight;
  }

  requestChatNextScrollToBottom(isPromptMessageSubmit);
});
// When streaming response is updated
document.addEventListener("htmx:sseMessage", function (event) {
  if (!(event.target.id?.startsWith("response-"))) return;
  render_markdown(event.target);
  requestChatNextScrollToBottom();
});
// When streaming response is finished
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id?.startsWith("message_"))) return;
  const message_text = event.target.querySelector(".message-text");
  if (message_text) {
    render_markdown(message_text);
  }

  // Reinitialize tooltips for new message elements (e.g., context indicator)
  initializeTooltips();
  requestChatNextScrollToBottom();

  const chatPrompt = document.querySelector("#chat-prompt");
  if (chatPrompt && !chat_tour_in_progress) {
    try {
      chatPrompt.focus({preventScroll: true});
    } catch (_) {
      chatPrompt.focus();
    }
  }
});

// Live OOB swaps (like message-actions updates during streaming) need tooltip
// hydration too; otherwise newly inserted context indicator tooltips stay inert
// until a later full-message swap.
document.addEventListener("htmx:oobAfterSwap", function (event) {
  const target = event.detail?.target;
  if (!target) return;
  initializeTooltips(target);
});

// When a saved message is replaced via a normal HTMX swap (for example,
// translating reasoning steps in place), hydrate markdown/reasoning immediately.
document.addEventListener("htmx:afterSwap", function (event) {
  const targetId = event.detail?.target?.id;
  if (!(targetId?.startsWith("message_"))) return;

  const messageEl = document.getElementById(targetId);
  if (messageEl?.querySelector('.message-inline-edit-form')) {
    applyInlineEditMessageDimensions(messageEl);
    return;
  }

  pendingInlineEditDimensions.delete(targetId);

  requestAnimationFrame(() => {
    const messageText = messageEl?.querySelector(".message-text");
    if (messageText) {
      render_markdown(messageText);
    }

    initializeTooltips();
    requestChatNextScrollToBottom(true);
  });
});

// When only the reasoning section is swapped (e.g., translate reasoning steps),
// re-render the widget without replacing the whole message bubble.
document.addEventListener("htmx:afterSwap", function (event) {
  const targetId = event.detail?.target?.id;
  if (!(targetId?.startsWith("reasoning-section-"))) return;

  requestAnimationFrame(() => {
    const reasoningSection = document.getElementById(targetId);
    if (reasoningSection) {
      render_reasoning(reasoningSection);
    }
    initializeTooltips();
    requestChatNextScrollToBottom();
  });
});
// Title updated
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id === "current-chat-title")) return;
  updatePageTitle();
});
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id === 'chat-history-list') {
    schedulePendingTitleRefresh();
  }
});
// When prompt input is focused, Enter sends message, unless Shift+Enter (newline)
document.addEventListener("keydown", function (event) {
  if (document.activeElement.id === "chat-prompt" && event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    document.querySelector("#send-button").click();
  }
});
// Accordion swapped
document.addEventListener("htmx:afterSwap", function (event) {
  if (event.detail?.target?.id !== "options-accordion") return;
  afterAccordionSwap();
});
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (event.detail?.target?.id !== "options-accordion") return;
  afterAccordionSwap();
});

// deletes the list item associated with the deleted chat
// also checks if the section is now empty and removes it
function deleteChatSection(button) {
  // get chat id based on id of button
  var chat_id = button.id.split("delete-chat-")[1];
  // remove the chat list item associated with the deleted chat
  var chat_list_item = document.getElementById('chat-list-item-' + chat_id);
  chat_list_item.remove();

  // remove the section if it is now empty
  var section_number = button.getAttribute('data-section-number');
  var chat_list = document.getElementById('chat-list-' + section_number);
  if (chat_list.children.length === 0) {
    var section = document.getElementById('section-' + section_number);
    section.remove();
  }
}

// Message actions
function thumbMessage(clickedBtn) {
  isThumbDown = clickedBtn.classList.contains("thumb-down");
  isClicked = clickedBtn.classList.contains("clicked");
  clickedBtn.blur();
  clickedBtn.classList.toggle("clicked");
  let message = clickedBtn.closest(".message-outer");
  message.querySelectorAll(".thumb-message-button").forEach(function (btn) {
    if (clickedBtn !== btn) {
      btn.classList.remove("clicked");
    }
  });
  if (isThumbDown && !isClicked) {
    // Show the bootstrap modal (#modal)
    const feedbackModal = new bootstrap.Modal(
      document.getElementById('modal'),
      {"backdrop": true, "focus": true, "keyboard": true}
    );
    feedbackModal.show();
  }
}

/** Paste richly formatted text.
 * From https://stackoverflow.com/questions/23934656/how-can-i-copy-rich-text-contents-to-the-clipboard-with-javascript/77305170#77305170
 *
 * @param {string} rich - the text formatted as HTML
 * @param {string} plain - a plain text fallback
 */
async function pasteRich(rich, plain) {
  // Shiny new Clipboard API, not fully supported in Firefox.
  // https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API#browser_compatibility
  const html = new Blob([rich], {type: "text/html"});
  const text = new Blob([plain], {type: "text/plain"});
  const data = new ClipboardItem({"text/html": html, "text/plain": text});
  await navigator.clipboard.write([data]);
}

function setCopyButtonSuccessState(btn, durationMs = 2200) {
  if (!btn) return;

  const messageActions = btn.closest(".message-actions");
  if (messageActions) {
    messageActions.classList.add("copy-feedback-active");
    if (messageActions.copyFeedbackTimer) {
      clearTimeout(messageActions.copyFeedbackTimer);
    }
  }

  btn.blur();
  btn.classList.add("clicked");

  const clearSuccessState = function () {
    btn.classList.remove("clicked");
    if (messageActions) {
      messageActions.classList.remove("copy-feedback-active");
      messageActions.copyFeedbackTimer = null;
    }
  };

  const timeoutId = setTimeout(clearSuccessState, durationMs);
  if (messageActions) {
    messageActions.copyFeedbackTimer = timeoutId;
  }
}

function getPrimaryCopyButton(source) {
  if (source?.classList?.contains("copy-message-button")) {
    return source;
  }

  return source?.closest(".copy-message-dropdown")?.querySelector(".copy-message-button") || null;
}

function closeCopyMessageMenu(source) {
  const toggle = source?.closest(".copy-message-dropdown")?.querySelector(".copy-message-menu-toggle");
  if (!toggle || typeof bootstrap === "undefined" || !bootstrap.Dropdown) {
    return;
  }

  bootstrap.Dropdown.getOrCreateInstance(toggle).hide();
}

function getMessageMarkdownText(message) {
  const markdownText = message?.querySelector(".message-text .markdown-text");
  const markdownData = markdownText?.dataset?.md;

  if (markdownData) {
    try {
      const parsed = JSON.parse(markdownData);
      if (typeof parsed === "string") {
        return normalizeSkillLinkTokens(normalizeSkillLinksInCodeFormatting(parsed)).trim();
      }
    } catch (_) {
      return String(markdownData).trim();
    }
  }

  return message?.querySelector(".message-text")?.innerText?.trim() || "";
}

function getMessageRichClipboardPayload(message) {
  // Create a clone of the element in JS so we can remove unwanted elements
  const messageTextClone = message.querySelector(".message-text").cloneNode(true);
  // Remove sources section
  const sources = messageTextClone.querySelector("div.sources");
  if (sources) {
    sources.remove();
  }
  // Remove reasoning summary section
  const reasoningSummary = messageTextClone.querySelector(".reasoning-summary");
  if (reasoningSummary) {
    reasoningSummary.remove();
  }
  // Remove reasoning data script tag
  const reasoningScript = messageTextClone.querySelector("script[id^='reasoning-data-']");
  if (reasoningScript) {
    reasoningScript.remove();
  }
  // Strip background colors and font information from copied HTML
  // This prevents pasting styled text with unwanted backgrounds
  messageTextClone.querySelectorAll("*").forEach(el => {
    el.style.removeProperty("background");
    el.style.removeProperty("background-color");
    el.style.removeProperty("font-family");
    el.style.removeProperty("color");
  });
  const clipboardWrapper = document.createElement("div");
  clipboardWrapper.appendChild(messageTextClone);
  return getTrimmedClipboardPayload(clipboardWrapper);
}

async function copyMessage(source, format = "rich") {
  const btn = getPrimaryCopyButton(source);
  const message = btn?.closest(".message-outer");
  if (!btn || !message) return;

  if (format === "markdown") {
    await navigator.clipboard.writeText(getMessageMarkdownText(message));
  } else {
    const {html: messageHtml, plain: messageText} = getMessageRichClipboardPayload(message);
    await pasteRich(messageHtml, messageText);
  }

  closeCopyMessageMenu(source);
  setCopyButtonSuccessState(btn);
}

function copyChatURL(event, btn) {
  if (event.detail.xhr.status === 200) {
    const response = JSON.parse(event.detail.xhr.responseText);
    if (response.chat_url) {
      navigator.clipboard.writeText(response.chat_url);
      btn.blur();
      btn.classList.add("clicked");
      dropdown = btn.closest('.dropdown-menu');
      setTimeout(function () {
        btn.classList.remove("clicked");
        dropdown.classList.remove('show');
      }, 600);
    }
  }
}

function copyCode(btn) {
  let message = btn.closest("pre").querySelector("code");
  let codeText = message.innerText;
  navigator.clipboard.writeText(codeText);
  btn.blur();
  btn.classList.add("clicked");
  setTimeout(function () {
    btn.classList.remove("clicked");
  }, 300);
}

/** Copies the text from the user's prompt to the text input.
*
* @param {HTMLButtonElement} btn - the edit button of a user prompt.
* @param {string} messageMode - the current chat mode
*/
function copyPromptToTextInput(btn, messageMode) {
  let message = btn.closest(".message-outer");
  let messageText = message.querySelector(".message-text").innerText;

  const inputArea = document.getElementById("chat-prompt");

  inputArea.value = messageText;

  inputArea.dispatchEvent(new Event('change'));
  inputArea.focus();
}

function closeSidebar(sidebarID, resizePrompt = true) {
  const sidebar = document.querySelector("#" + sidebarID);
  const toggle = document.querySelector("#" + sidebarID + "-toggle");
  if (sidebar) {
    if (sidebarID === "left-sidebar") {
      sidebar.classList.add("collapsed");
      sidebar.classList.remove("hidden");
    } else {
      sidebar.classList.add("hidden");
    }
  }
  if (toggle) {
    if (sidebarID === "left-sidebar") {
      toggle.classList.add("hidden");
    } else {
      toggle.classList.remove("hidden");
    }
  }
  if (sidebarID === "left-sidebar") {
    syncLeftSidebarBackdrop();
  }
  if (resizePrompt) {
    resizePromptContainer();
  }
}

function openSidebar(sidebarID, resizePrompt = true) {
  const sidebar = document.querySelector("#" + sidebarID);
  const toggle = document.querySelector("#" + sidebarID + "-toggle");
  if (sidebar) {
    sidebar.classList.remove("collapsed");
    sidebar.classList.remove("hidden");
  }
  if (toggle) {
    toggle.classList.add("hidden");
  }
  if (sidebarID === "left-sidebar") {
    syncLeftSidebarBackdrop();
  }
  if (resizePrompt) {
    resizePromptContainer();
  }
}

const closeLeftSidebarButton = document.querySelector("#close-left-sidebar");
if (closeLeftSidebarButton) {
  closeLeftSidebarButton.addEventListener("click", function () {closeSidebar("left-sidebar");});
}

const leftSidebarToggleButton = document.querySelector("#left-sidebar-toggle");
if (leftSidebarToggleButton) {
  leftSidebarToggleButton.addEventListener("click", function () {openSidebar("left-sidebar");});
}

// Reload the page if navigated to with browser back / forward buttons
// This addresses a bug where the chat does not include all messages
// See https://stackoverflow.com/a/56851042
if (performance.getEntriesByType("navigation")[0].type === "back_forward") {
  location.reload();
}

function cancelChatRename() {
  document.querySelectorAll(".cancel-chat-rename-btn").forEach(function (btn) {
    btn.click();
  });
}


function updatePageTitle(title = null) {
  if (title) {
    document.title = title;
    return;
  }
  const new_page_title = document.querySelector("#current-chat-title").dataset.pagetitle;
  if (new_page_title) document.title = new_page_title;
}

function emailChatAuthor(url) {
  htmx.ajax('GET', url, {target: '#author-mailto-container', swap: 'innerHTML'}).then(
    function () {
      document.querySelector("#author-mailto-container a").click();
      document.querySelector("#author-mailto-container").innerHTML = '';
    }
  );
}

// Show "Expand all" only if there's something truncated, right before the menu opens
document.addEventListener('show.bs.dropdown', (e) => {
  const id = e.target.id?.split('dropdownMenuButton-')[1];
  if (!id) return;
  const hasTruncated = !!document.querySelector('.message-outer.truncate:not(.show-all)');
  const expandAllBtn = document.getElementById(`expand-all-btn-${id}`);
  if (expandAllBtn) {
    expandAllBtn.classList.toggle('d-none', !hasTruncated);
  }
});

function expandAllMessages(chat_id) {
  // Find all truncated messages that haven't been expanded yet
  const truncatedMessages = document.querySelectorAll('.message-outer.truncate:not(.show-all)');
  // Add 'show-all' class to expand all truncated messages
  truncatedMessages.forEach(function (message) {
    message.classList.add('show-all');
  });

  // Toggle buttons visibility
  const expandBtn = document.querySelector(`#expand-all-btn-${chat_id}`);
  const collapseBtn = document.querySelector(`#collapse-all-btn-${chat_id}`);
  if (expandBtn) expandBtn.classList.add('d-none');
  if (collapseBtn) collapseBtn.classList.remove('d-none');

  scheduleChatLayoutPositionUpdate();
}

function collapseAllMessages(chat_id) {
  // Find all expanded messages
  const expandedMessages = document.querySelectorAll('.message-outer.show-all');

  // Remove 'show-all' class to collapse all expanded messages
  expandedMessages.forEach(function (message) {
    message.classList.remove('show-all');
  });

  // Toggle buttons visibility
  const expandBtn = document.querySelector(`#expand-all-btn-${chat_id}`);
  const collapseBtn = document.querySelector(`#collapse-all-btn-${chat_id}`);
  if (expandBtn) expandBtn.classList.remove('d-none');
  if (collapseBtn) collapseBtn.classList.add('d-none');

  scheduleChatLayoutPositionUpdate();
}

function initializeReasoningEffortToggle() {
  if (typeof toggleReasoningEffort !== 'function' || typeof toggleVerbosity !== 'function') {
    return;
  }

  toggleReasoningEffort();
  toggleVerbosity();

  const modelSelect = document.getElementById('id_chat_model');
  if (modelSelect) {
    modelSelect.addEventListener('change', toggleReasoningEffort);
    modelSelect.addEventListener('change', toggleVerbosity);
  }
}

function afterAccordionSwap() {
  initializeReasoningEffortToggle();
  initializeTooltips();
}

// Printing
function printChat() {
  // Expand all messages
  expandAllMessages(chat_id);
  // Wait a bit for the messages to expand
  setTimeout(() => {
    window.print();
  }, 300);
}

// Hijack Ctrl+P / Cmd+P to print the chat
window.addEventListener("keydown", function (event) {
  if ((event.ctrlKey) && event.key === "p") {
    event.preventDefault();
    printChat();
  }
});

function isModifiedClick(event) {
  return !!(
    event && (
      event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
      || event.button !== 0
    )
  );
}

function hasActiveLibrarianUpload() {
  const librarianUploadForm = document.getElementById('librarian-upload-form');
  if (!librarianUploadForm) {
    return false;
  }

  return Array.from(librarianUploadForm.querySelectorAll('.dff-file')).some((file) => {
    return !file.classList.contains('dff-upload-success') && !file.classList.contains('dff-upload-fail');
  });
}

function shouldIgnoreLibrarianModalInteraction(target) {
  if (!target) {
    return false;
  }

  const inSharedLibrarianModal = target.closest('#chat-next-modal.show [data-chat-next-modal-view="librarian"]');

  if (!inSharedLibrarianModal) {
    return false;
  }

  return !hasActiveLibrarianUpload();
}

function setUploadsInProgress(state) {
  if (state) {
    document.addEventListener('click', navigationClickHandler, true);
    window.addEventListener('beforeunload', beforeUnloadHandler);
  } else {
    document.removeEventListener('click', navigationClickHandler, true);
    window.removeEventListener('beforeunload', beforeUnloadHandler);
  }
}

function navigationClickHandler(e) {
  // ignore clicks on the file upload progress bar, modals, download links, and new-tab links
  if (e.target.closest('.dff-cancel') || e.target.closest('[data-bs-toggle="modal"]') || e.target.closest('a[download]') || e.target.closest('a[target="_blank"]')) {
    return;
  }

  // intercept clicks on navigation elements
  const target = e.target.closest('a[href], button[hx-get], button[hx-post], .nav-link, .list-group-item');

  if (target && shouldIgnoreLibrarianModalInteraction(target)) {
    return;
  }

  if (target && !isModifiedClick(e)) {
    e.preventDefault();
    e.stopPropagation();

    const confirmLeave = confirm(CANCEL_UPLOAD_WARNING);
    if (confirmLeave) {
      setUploadsInProgress(false);
      // Re-trigger the click
      target.click();
    }
  }
}

function beforeUnloadHandler(event) {
  event.preventDefault();
}

document.addEventListener('htmx:oobAfterSwap', function (event) {
  // After chat upload is complete, an hx-swap-oob hides/empties #chat-upload-message
  if (event.detail?.target?.id === 'chat-upload-message') {
    setUploadsInProgress(false);
  }
});

// Highlight a term within only text nodes of a root element; skips code/KaTeX blocks
function highlightTermInElement(root, term) {
  if (!root || !term) return null;
  const skipSelector = 'code, pre, kbd, samp, .katex, .MathJax, .hljs, .no-highlight';
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      if (node.parentElement && node.parentElement.closest(skipSelector)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });

  const regex = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  let firstMark = null;
  const toProcess = [];
  while (walker.nextNode()) toProcess.push(walker.currentNode);

  for (const textNode of toProcess) {
    const text = textNode.nodeValue;
    if (!regex.test(text)) continue;
    regex.lastIndex = 0; // reset after test
    const frag = document.createDocumentFragment();
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(text)) !== null) {
      const start = match.index;
      const end = start + match[0].length;
      if (start > lastIndex) frag.appendChild(document.createTextNode(text.slice(lastIndex, start)));
      const mark = document.createElement('mark');
      mark.className = 'search-hit';
      mark.textContent = text.slice(start, end);
      frag.appendChild(mark);
      if (!firstMark) firstMark = mark;
      lastIndex = end;
    }
    if (lastIndex < text.length) frag.appendChild(document.createTextNode(text.slice(lastIndex)));
    textNode.parentNode.replaceChild(frag, textNode);
  }
  return firstMark;
}

// Remove <mark class="search-hit"> wrappers, keeping inner text
function unmarkInElement(root) {
  if (!root) return;
  const marks = root.querySelectorAll('mark.search-hit');
  marks.forEach(mark => {
    const text = document.createTextNode(mark.textContent);
    mark.replaceWith(text);
  });
}

/**
 * Handle SSE errors for chat streaming responses.
 * When an SSE connection fails (and sse-no-retry is set), first check if the
 * server has already completed the response. If so, fetch and display it.
 * Otherwise, show a user-friendly error message with a retry button.
 *
 * This handles the race condition where the server finishes streaming before
 * the client finishes processing all SSE events (especially with long responses
 * that require expensive markdown re-rendering on each chunk).
 */
document.body.addEventListener('htmx:sseError', function (event) {
  const element = event.target;

  // Only handle chat streaming response elements
  if (!element.classList.contains('chat-streaming-response')) {
    return;
  }

  // Close the EventSource to ensure no reconnection attempts
  const source = event.detail.source;
  if (source) {
    source.close();
  }

  const messageId = element.dataset.messageId;
  const messageHtmlUrl = element.dataset.messageHtmlUrl;

  // If we have a message ID, check if the server already completed the response
  if (messageId && messageHtmlUrl) {
    // Keep current content visible while we check - no loading state
    // Use HTMX to fetch the completed message
    htmx.ajax('GET', messageHtmlUrl, {
      handler: function (elt, responseInfo) {
        try {
          const data = JSON.parse(responseInfo.xhr.responseText);
          if (data.complete && data.html) {
            // Server has the complete response - use HTMX to swap it
            // The HTML contains hx-swap-oob="true" on the message element
            // Use htmx.swap to process OOB swaps properly
            const tempContainer = document.createElement('div');
            htmx.swap(tempContainer, data.html, {swapStyle: 'innerHTML'});

            requestAnimationFrame(() => {
              const savedMessage = document.getElementById(`message_${messageId}`);
              const messageText = savedMessage?.querySelector('.message-text');
              if (messageText) {
                render_markdown(messageText);
              }
              initializeTooltips();
              pinCompletedStreamToBottom(messageId);
            });

            // Hide the stop button since streaming is complete
            const stopButton = document.getElementById('stop-button');
            if (stopButton) {
              stopButton.innerHTML = '';
            }
          } else {
            // Message not complete - show error UI
            showSSEErrorUI(element, messageId);
          }
        } catch (e) {
          // Parse error - show error UI
          showSSEErrorUI(element, messageId);
        }
      }
    });
  } else {
    // No message ID - show error UI immediately
    showSSEErrorUI(element, null);
  }
});

/**
 * Show the SSE error UI with retry button.
 */
function showSSEErrorUI(element, messageId) {
  const errorText = element.dataset.errorText || t('sse_connection_lost', 'Connection lost. The response may be incomplete.');
  const retryText = element.dataset.retryText || t('retry', 'Retry');
  const sseConnectUrl = element.getAttribute('sse-connect');

  element.innerHTML = `
    <div class="alert alert-warning d-flex align-items-center gap-2 mb-0" role="alert">
      <i class="bi bi-exclamation-triangle-fill"></i>
      <span>${errorText}</span>
      ${messageId ? `
        <button type="button" 
                class="btn btn-sm btn-warning ms-auto"
                onclick="retrySSEConnection(this, '${messageId}', '${sseConnectUrl}')">
          <i class="bi bi-arrow-clockwise"></i> ${retryText}
        </button>
      ` : ''}
    </div>
  `;

  // Also hide the stop button since streaming has stopped
  const stopButton = document.getElementById('stop-button');
  if (stopButton) {
    stopButton.innerHTML = '';
  }
}

/**
 * Retry an SSE connection after an error.
 * Re-creates the SSE element to trigger a fresh connection.
 */
function retrySSEConnection(button, messageId, sseConnectUrl) {
  const responseDiv = document.getElementById(`response-${messageId}`);
  if (!responseDiv) return;

  // Show loading state
  responseDiv.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';

  // Re-initialize the SSE connection by triggering HTMX to process the element again
  // We need to temporarily remove and re-add the sse-connect attribute
  responseDiv.removeAttribute('sse-connect');

  // Use setTimeout to ensure the attribute removal is processed
  setTimeout(() => {
    responseDiv.setAttribute('sse-connect', sseConnectUrl);
    // Trigger HTMX to process the element
    htmx.process(responseDiv);
  }, 10);
}

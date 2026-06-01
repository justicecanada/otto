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
});
md.use(katexPlugin);
md.renderer.rules.table_open = () => '<table class="table">\n';

// Global timestamp for syncing typing animations across HTMX swaps
// The animation has a 1.5s duration, so we calculate where in the cycle we should be
const typingAnimationStart = performance.now();
const TYPING_ANIMATION_DURATION = 1500; // 1.5s in ms
const STREAMING_RENDER_LARGE_MESSAGE_THRESHOLD = 8000;
const STREAMING_RENDER_MIN_INTERVAL_MS = 120;
const STREAMING_RENDER_MAX_WAIT_MS = 400;
const STREAMING_COMPLETION_RETRY_ATTEMPTS = 5;
const STREAMING_COMPLETION_RETRY_DELAY_MS = 350;
const streamingRenderState = new Map();

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

// Bounded chat title polling: max 6 attempts (15 seconds total at 2.5s intervals)
// Prevents indefinite polling if workers are down or titles never resolve.
const TITLE_POLL_MAX_ATTEMPTS = 6;
const TITLE_POLL_INTERVAL_MS = 2500;
let chatTitlePollAttempts = 0;
let chatTitlePollTimer = null;

function collectPendingTitleChatIds() {
  return Array.from(document.querySelectorAll('#chat-history-list .chat-list-item[data-title-pending="true"]'))
    .map((el) => el.id?.replace('chat-list-item-', ''))
    .filter(Boolean);
}

function refreshPendingChatTitles() {
  const pendingIds = collectPendingTitleChatIds();

  // Stop if: no pending items OR we've exceeded max attempts
  if (!pendingIds.length || chatTitlePollAttempts >= TITLE_POLL_MAX_ATTEMPTS) {
    clearTimeout(chatTitlePollTimer);
    chatTitlePollTimer = null;
    chatTitlePollAttempts = 0;
    return;
  }

  // Increment attempt counter
  chatTitlePollAttempts++;

  const currentChatId = document.getElementById('current-chat-hidden-field')?.value;
  if (!currentChatId) return;

  const params = new URLSearchParams();
  pendingIds.forEach((id) => params.append('chat_ids', id));
  const searchVal = document.getElementById('chat-history-search-input')?.value || '';
  if (searchVal) {
    params.set('search', searchVal);
  }

  const url = `/chat/id/${encodeURIComponent(currentChatId)}/refresh_titles/?${params.toString()}`;

  // Use plain fetch so OOB swaps are applied reliably (htmx.ajax + swap:'none' can drop them)
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
        }
      });
      if (updatedCurrentTitle) updatePageTitle();
    })
    .catch(() => { });

  // Always schedule next poll (will exit at function start if conditions met)
  chatTitlePollTimer = setTimeout(refreshPendingChatTitles, TITLE_POLL_INTERVAL_MS);
}

function schedulePendingTitleRefresh() {
  // Stop any existing polling
  if (chatTitlePollTimer) {
    clearTimeout(chatTitlePollTimer);
  }

  // Reset attempt counter and schedule first poll with delay
  chatTitlePollAttempts = 0;
  // Start polling after first interval to avoid immediate hammer
  chatTitlePollTimer = setTimeout(refreshPendingChatTitles, TITLE_POLL_INTERVAL_MS);
}

// Global state for reasoning widget - tracks expansion state per message
const reasoningState = new Map();

/**
 * Render reasoning widget from streaming data or saved message data.
 * 
 * During streaming: 
 * - The widget container (reasoning-container-{id}) is placed OUTSIDE the SSE swap area
 * - Data updates come via OOB swap to a hidden div (reasoning-data-{id})
 * - This function reads from the data div and updates the persistent widget
 * 
 * For saved messages: reads from reasoning-summary div's data attributes.
 */
function render_reasoning(element) {
  // Check if this is a streaming response element (response-{id})
  if (element.id && element.id.startsWith('response-')) {
    const messageId = element.id.replace('response-', '');
    // Find the sibling data and container elements by ID
    const dataEl = document.getElementById(`reasoning-data-${messageId}`);
    const container = document.getElementById(`reasoning-container-${messageId}`);
    if (dataEl && container) {
      renderReasoningFromData(container, dataEl, messageId);
    }
    return;
  }

  // Check for streaming containers within element (for initial page load cases)
  const containers = element.querySelectorAll('.reasoning-widget-container[id^="reasoning-container-"]');
  containers.forEach(container => {
    const messageId = container.id.replace('reasoning-container-', '');
    const dataEl = document.getElementById(`reasoning-data-${messageId}`);
    if (dataEl) {
      renderReasoningFromData(container, dataEl, messageId);
    }
  });

  // Check for saved message data (reasoning-summary div)
  const reasoningSummary = element.querySelector(".reasoning-summary");
  if (reasoningSummary) {
    renderReasoningFromSaved(element, reasoningSummary);
  }
}

/**
 * Handle reasoning during SSE streaming.
 * Data comes from a hidden div (updated via OOB swap), widget is rendered into a persistent container.
 */
function renderReasoningFromData(container, dataEl, messageId) {
  // Parse reasoning data from data element
  let steps = [];
  const reasoningData = dataEl.dataset.reasoning;
  if (reasoningData) {
    try {
      steps = JSON.parse(reasoningData);
    } catch (e) {
      steps = [];
    }
  }

  // Get text options from the container (set in template)
  const thinkingText = container.dataset.thinkingText || "Thinking...";
  const showReasoningText = container.dataset.showReasoningText || "Show processing steps";
  const generatingText = container.dataset.generatingText || "Generating final response";
  const reasoningStepsText = container.dataset.reasoningStepsText || "Processing steps";
  const emptyReasoningText = container.dataset.emptyReasoningText || "If the model provides reasoning steps, they will be shown here shortly.";

  const isReasoning = dataEl.dataset.isReasoning === "true";

  // Check if response text has started streaming by looking for .markdown-text element
  // When LLM starts generating text, it wraps it in a .markdown-text div
  const responseEl = document.getElementById(`response-${messageId}`);
  const hasResponseText = responseEl && responseEl.querySelector('.markdown-text') !== null;

  // Get or initialize state
  // During streaming, isFinished stays false
  // hasText tracks whether response text has started (for "Generating final response" state)
  // When streaming ends, renderReasoningFromSaved will be called which sets isFinished=true
  let state = reasoningState.get(messageId);
  if (!state) {
    state = {expanded: false, isFinished: false, hasText: false};
    reasoningState.set(messageId, state);
  }

  // Update hasText state - once text starts, it stays true until finished
  if (hasResponseText) {
    state.hasText = true;
  }

  // Hide widget if no steps and not reasoning (no widget needed)
  if (!isReasoning && steps.length === 0) {
    container.innerHTML = "";
    return;
  }

  // Check if widget exists, create if not
  let widget = container.querySelector('.reasoning-widget');
  if (!widget) {
    widget = createReasoningWidget(messageId, state);
    container.appendChild(widget);
  }

  // Update widget content
  updateReasoningContent(widget, steps, isReasoning, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText);
}

/**
 * Handle reasoning for saved/loaded messages.
 * Data comes from reasoning-summary div attributes or script tag.
 */
function renderReasoningFromSaved(element, reasoningSummary) {
  const messageId = reasoningSummary.id.replace('reasoning-', '');

  // Get data from inline attribute or script tag
  let reasoningData = reasoningSummary.dataset.reasoning;
  if (!reasoningData) {
    const scriptTag = document.getElementById(`reasoning-data-${messageId}`);
    if (scriptTag) {
      reasoningData = scriptTag.textContent;
    }
  }

  let steps = [];
  if (reasoningData) {
    try {
      steps = JSON.parse(reasoningData);
    } catch (e) {
      steps = [];
    }
  }

  // Hide if no data
  if (steps.length === 0) {
    reasoningSummary.innerHTML = "";
    return;
  }

  const thinkingText = reasoningSummary.dataset.thinkingText || "Thinking...";
  const showReasoningText = reasoningSummary.dataset.showReasoningText || "Show processing steps";
  const reasoningStepsText = reasoningSummary.dataset.reasoningStepsText || "Processing steps";
  const emptyReasoningText = reasoningSummary.dataset.emptyReasoningText || "If the model provides reasoning steps, they will be shown here shortly.";

  // Get or initialize state (saved messages are always finished)
  // Important: if state exists from streaming, update it to finished
  let state = reasoningState.get(messageId);
  if (!state) {
    state = {expanded: false, isFinished: true, hasText: false};
    reasoningState.set(messageId, state);
  } else {
    // Update existing state to finished
    state.isFinished = true;
  }

  // Check if widget exists
  let widget = reasoningSummary.querySelector('.reasoning-widget');
  if (!widget) {
    widget = createReasoningWidget(messageId, state);
    reasoningSummary.appendChild(widget);
  }

  const generatingText = reasoningSummary.dataset.generatingText || "Generating final response";

  // Update widget content
  updateReasoningContent(widget, steps, false, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText);
}

/**
 * Create the reasoning widget DOM structure.
 */
function createReasoningWidget(messageId, state) {
  const widget = document.createElement('div');
  widget.className = 'reasoning-widget';

  const header = document.createElement('button');
  header.className = 'reasoning-header' + (state.expanded ? '' : ' collapsed');
  header.type = 'button';
  header.innerHTML = `
    <span class="reasoning-icon"><i class="bi bi-chat-dots"></i></span>
    <span class="reasoning-title"></span>
    <span class="reasoning-toggle"><i class="bi bi-chevron-right"></i></span>
  `;

  const content = document.createElement('div');
  content.className = 'reasoning-content' + (state.expanded ? '' : ' collapsed');
  content.innerHTML = '<div class="reasoning-steps-list"></div>';

  // Toggle handler
  header.addEventListener('click', function (e) {
    e.preventDefault();
    state.expanded = !state.expanded;
    header.classList.toggle('collapsed', !state.expanded);
    content.classList.toggle('collapsed', !state.expanded);
    // Update header text based on new state
    // Pass false for allStepsComplete since toggling happens after streaming
    const titleEl = header.querySelector('.reasoning-title');
    if (titleEl) {
      titleEl.textContent = getHeaderText(state, header.dataset.lastStepTitle,
        header.dataset.thinkingText, header.dataset.showReasoningText, header.dataset.generatingText, header.dataset.reasoningStepsText, false);
    }
  });

  widget.appendChild(header);
  widget.appendChild(content);

  return widget;
}

/**
 * Update the reasoning widget content without replacing the widget itself.
 */
function updateReasoningContent(widget, steps, isReasoning, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText) {
  const header = widget.querySelector('.reasoning-header');
  const stepsList = widget.querySelector('.reasoning-steps-list');
  if (!header || !stepsList) return;

  // Check if all steps are complete (progress events finished, waiting for LLM)
  // Steps with status "complete" are progress events; steps without status are API reasoning steps
  const hasProgressEvents = steps.some(s => s.status !== undefined);
  const allProgressComplete = hasProgressEvents && steps.every(s => s.status === undefined || s.status === 'complete');
  const hasApiReasoningSteps = steps.some(s => s.status === undefined);
  const allStepsComplete = allProgressComplete && !hasApiReasoningSteps && !state.hasText;

  // Store text options on header for toggle handler to use
  header.dataset.thinkingText = thinkingText;
  header.dataset.showReasoningText = showReasoningText;
  header.dataset.generatingText = generatingText;
  header.dataset.reasoningStepsText = reasoningStepsText;
  header.dataset.emptyReasoningText = emptyReasoningText || 'Processing steps will appear here shortly.';
  header.dataset.lastStepTitle = steps.length > 0 ? steps[steps.length - 1].title : '';

  // Update header text
  const titleEl = header.querySelector('.reasoning-title');
  if (titleEl) {
    titleEl.textContent = getHeaderText(state, header.dataset.lastStepTitle, thinkingText, showReasoningText, generatingText, reasoningStepsText, allStepsComplete);
  }

  // Update pulse animation
  if (isReasoning) {
    header.classList.add('reasoning-active');
  } else {
    header.classList.remove('reasoning-active');
  }

  // Update steps list
  let stepsHtml = '';
  if (steps.length === 0) {
    // Show empty state message
    stepsHtml = `<div class="reasoning-step-empty text-muted fst-italic">${escapeHtml(header.dataset.emptyReasoningText)}</div>`;
  } else {
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      stepsHtml += `
        <div class="reasoning-step">
          <div class="reasoning-step-title">
            <span class="reasoning-step-number">${i + 1}.</span>
            <span class="reasoning-step-text">${escapeHtml(step.title)}</span>
          </div>
          ${step.details ? `<div class="reasoning-step-details">${escapeHtml(step.details)}</div>` : ''}
        </div>
      `;
    }
  }
  stepsList.innerHTML = stepsHtml;
}

/**
 * Determine header text based on current state.
 */
function getHeaderText(state, lastStepTitle, thinkingText, showReasoningText, generatingText, reasoningStepsText, allStepsComplete) {
  if (state.expanded) {
    return reasoningStepsText;
  } else if (state.isFinished) {
    return showReasoningText;
  } else if (state.hasText) {
    // Response text is streaming - show "Generating final response"
    return generatingText;
  } else if (allStepsComplete) {
    // All progress events complete, waiting for LLM response - show "Thinking"
    return thinkingText;
  } else if (lastStepTitle) {
    return lastStepTitle;
  } else {
    return thinkingText;
  }
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
});
md_with_html.use(katexPlugin);
md_with_html.renderer.rules.table_open = () => '<table class="table">\n';

// Observe message content height changes so truncation can be detected
// even after complex nested HTML (code blocks, tables, KaTeX, etc.) finish layout.
const messageResizeObserver = (typeof ResizeObserver !== 'undefined') ? new ResizeObserver((entries) => {
  entries.forEach(entry => {
    const el = entry.target;
    if (!el) return;

    // Clean up observation if element is no longer in the DOM
    if (!el.isConnected) {
      messageResizeObserver.unobserve(el);
      delete el._resizeObserverAttached;
      return;
    }

    const outer = el.closest('.message-outer');
    if (!outer) return;
    try {
      if (el.offsetHeight < el.scrollHeight) {
        outer.classList.add('truncate');
      } else {
        outer.classList.remove('truncate');
      }
    } catch (e) {
      // Element may be detached during observation
      if (console && console.warn) {
        console.warn('Error checking truncation:', e);
      }
    }
  });
}) : null;

function checkTruncation(element) {
  if (!element) return;
  const outer = element.closest('.message-outer');
  if (!outer) return;

  // We try to check immediately
  try {
    if (element.offsetHeight < element.scrollHeight) {
      outer.classList.add('truncate');
    } else {
      outer.classList.remove('truncate');
    }
  } catch (e) {
    if (console && console.warn) {
      console.warn('Error in initial truncation check:', e);
    }
  }

  // Start observing element for later layout changes (attach only once)
  try {
    if (messageResizeObserver && !element._resizeObserverAttached) {
      messageResizeObserver.observe(element);
      element._resizeObserverAttached = true;
    }
  } catch (e) {
    if (console && console.warn) {
      console.warn('Error attaching ResizeObserver:', e);
    }
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function render_markdown(element) {
  // Render reasoning summary first
  render_reasoning(element);

  // Render markdown in the element
  const markdown_text = element.querySelector(".markdown-text");
  dot_element = element.querySelector(".typing"); // Exists when dots=True on htmx_stream call

  // Sync typing animation to global timeline so dots don't reset on HTMX swaps
  if (dot_element) {
    syncTypingAnimation(dot_element);
  }

  if (markdown_text) {
    let to_parse = markdown_text.dataset.md;
    try {
      to_parse = JSON.parse(to_parse);
    } catch (e) {
      to_parse = false;
    }
    const parent = markdown_text.parentElement;
    if (to_parse) {
      // IMPORTANT: Only replace markdown_text content, not the entire parent
      // This preserves the reasoning-summary sibling element
      markdown_text.innerHTML = md.render(to_parse).replaceAll('&lt;br&gt;', '<br>').replace('<a href=', '<a target="_blank" href=');
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
    } else if ((after_text = parent.nextElementSibling)) {
      // If stream is empty (which should only happen between batches), it will stream dots
      // so we can remove the dot element we manually added above
      if (after_text.classList.contains("typing")) {
        after_text.remove();
      }
    }
  }
}

function renderAllMarkdown(root = document) {
  const elements = new Set();
  if (root?.classList?.contains('message-text')) {
    elements.add(root);
  }
  if (root?.querySelectorAll) {
    root.querySelectorAll('div.message-text').forEach((element) => elements.add(element));
  }
  elements.forEach((element) => {
    render_markdown(element);
    checkTruncation(element);
  });
}

function clearStreamingRenderState(messageId) {
  const state = streamingRenderState.get(messageId);
  if (!state) return;
  if (state.timer) {
    clearTimeout(state.timer);
  }
  streamingRenderState.delete(messageId);
}

function flushStreamingRender(messageId) {
  const state = streamingRenderState.get(messageId);
  if (!state) return;
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
  const element = state.latestElement;
  if (!element || !element.isConnected) {
    clearStreamingRenderState(messageId);
    return;
  }
  render_markdown(element);
  const renderedMarkdown = element.querySelector('.markdown-text');
  if (renderedMarkdown) {
    state.lastRenderedHTML = renderedMarkdown.innerHTML;
  }
  const messageText = element.closest('.message-text');
  if (messageText) {
    checkTruncation(messageText);
  }
  scrollToBottom(false, false);
  state.lastRenderedAt = performance.now();
  state.firstQueuedAt = 0;
}

function scheduleStreamingRender(element) {
  if (!(element?.id?.startsWith('response-'))) return;

  const messageId = element.id.replace('response-', '');
  const markdownText = element.querySelector('.markdown-text');
  const renderPayload = markdownText?.dataset?.md || '';

  if (renderPayload.length < STREAMING_RENDER_LARGE_MESSAGE_THRESHOLD) {
    clearStreamingRenderState(messageId);
    render_markdown(element);
    const messageText = element.closest('.message-text');
    if (messageText) {
      checkTruncation(messageText);
    }
    scrollToBottom(false, false);
    return;
  }

  const now = performance.now();
  const state = streamingRenderState.get(messageId) || {
    timer: null,
    latestElement: null,
    lastRenderedAt: 0,
    firstQueuedAt: 0,
    lastRenderedHTML: '',
  };
  state.latestElement = element;
  if (!state.firstQueuedAt) {
    state.firstQueuedAt = now;
  }
  streamingRenderState.set(messageId, state);

  const elapsedSinceRender = now - state.lastRenderedAt;
  const queuedFor = now - state.firstQueuedAt;

  if (
    elapsedSinceRender >= STREAMING_RENDER_MIN_INTERVAL_MS
    || queuedFor >= STREAMING_RENDER_MAX_WAIT_MS
  ) {
    flushStreamingRender(messageId);
    return;
  }

  if (state.lastRenderedHTML) {
    const currentMarkdownText = markdownText || element.querySelector('.markdown-text');
    if (currentMarkdownText && !currentMarkdownText.innerHTML) {
      currentMarkdownText.innerHTML = state.lastRenderedHTML;
    }
  }

  if (!state.timer) {
    state.timer = setTimeout(() => {
      flushStreamingRender(messageId);
    }, Math.max(0, STREAMING_RENDER_MIN_INTERVAL_MS - elapsedSinceRender));
  }
}

function hideStreamingStopButton() {
  const stopButton = document.getElementById('stop-button');
  if (stopButton) {
    stopButton.innerHTML = '';
  }
}

async function recoverCompletedMessage(messageId) {
  for (let attempt = 0; attempt < STREAMING_COMPLETION_RETRY_ATTEMPTS; attempt++) {
    try {
      const response = await fetch(`/chat/message/${messageId}/html/`, {
        credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      if (response.ok) {
        const data = await response.json();
        if (data.complete && data.html) {
          const tempContainer = document.createElement('div');
          htmx.swap(tempContainer, data.html, {swapStyle: 'innerHTML'});
          const swappedMessage = document.getElementById(`message_${messageId}`);
          renderAllMarkdown(swappedMessage || document);
          hideStreamingStopButton();
          return true;
        }
      }
    } catch (_) {
      // Ignore and retry below.
    }

    if (attempt < STREAMING_COMPLETION_RETRY_ATTEMPTS - 1) {
      await new Promise((resolve) => setTimeout(resolve, STREAMING_COMPLETION_RETRY_DELAY_MS));
    }
  }

  return false;
}

// Chat window UI
let autoscroll = true;
const scrollBtn = document.querySelector("#scroll-btn");

document.querySelector("#chat-container").addEventListener("scroll", function () {
  const threshold = 10; // pixels from the bottom considered "at the bottom"
  if ((this.scrollHeight - this.scrollTop - this.clientHeight) > threshold) {
    autoscroll = false;
    scrollBtn?.classList.add("show");
  } else {
    autoscroll = true;
    scrollBtn?.classList.remove("show");
  }
});

const copyCodeButtonHTML = `<button type="button" onclick="copyCode(this)"
class="btn btn-link m-0 p-0 text-muted copy-message-button copy-button"
title="Copy"><i class="bi bi-copy"></i><i class="bi bi-check-lg"></i></button>`;

function scrollToBottom(smooth = true, force = false) {
  resizePromptContainer();
  if (!autoscroll && !force) {
    return;
  }
  let messagesContainer = document.querySelector("#chat-container");
  let destination = messagesContainer.scrollHeight;
  // If there is currently a response streaming, disable smooth
  if (document.querySelector(".chat-streaming-response")) {
    smooth = false;
  }
  if (smooth) {
    messagesContainer.scrollTo({
      top: destination,
      behavior: "smooth"
    });
    return;
  }
  messagesContainer.scrollTop = destination;
}

function scrollToListItem() {
  setTimeout(() => {
    const historyList = document.getElementById('chat-history-list');
    if (!historyList) return;

    const currentChat = historyList.querySelector('.chat-list-item.current');
    if (!currentChat) return;

    // Only scroll the sidebar list container (not outer/page ancestors).
    const targetTop = currentChat.offsetTop - (historyList.clientHeight / 2) + (currentChat.offsetHeight / 2);
    historyList.scrollTo({
      top: Math.max(0, targetTop),
      behavior: 'auto'
    });
  }, 100);
}

function updatePlaceholder(mode) {
  // Update placeholder text
  const chat_prompt = document.querySelector("#chat-prompt");
  chat_prompt.placeholder = chat_prompt.dataset[`${mode}Placeholder`];
}


function confirmRerun(btn, evt) {
  const messageOuter = btn.closest('.message-outer');
  const messageId = messageOuter.id.substring('message_'.length);

  const messages = Array.from(document.querySelectorAll('#messages-container .message-outer'));
  const lastUserMsg = [...messages].reverse().find(el => (el.dataset.isBot || '').toLowerCase() === 'false');
  const lastUserMsgId = lastUserMsg ? lastUserMsg.id.substring('message_'.length) : null;

  // if the user is rerunning their last message, no need to confirm
  if (!lastUserMsg || messageId === lastUserMsgId) {
    return;
  }

  const confirmMessage = btn.dataset.confirmText;
  if (!window.confirm(confirmMessage)) {
    evt.preventDefault();
    evt.stopImmediatePropagation();
  }
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

function handleModeChange(mode, element = null) {
  // Set the hidden input value to the selected mode
  let hidden_mode_input = document.querySelector('#id_mode');
  hidden_mode_input.value = mode;
  // Set the #chat-outer class to the selected mode for mode-specific styling
  document.querySelector('#chat-outer').classList = [mode];
  triggerOptionSave();
  updatePlaceholder(mode);
  resizeOtherElements();
  updateRerunPromptButtons();
  // Always update the accordion to ensure exactly one section is expanded
  updateAccordion(mode);
}

function updateAccordion(mode) {
  // Ensure exactly one accordion section is expanded - the one matching the current mode
  // Use Bootstrap Collapse API for smooth animations
  let accordion_parent = document.querySelector("#options-accordion");
  for (chat_mode of ["chat", "summarize", "translate", "qa"]) {
    let accordion_button = accordion_parent.querySelector(`button[aria-controls="options-accordion-${chat_mode}"]`);
    let accordion_content = document.querySelector(`#options-accordion-${chat_mode}`);
    let bsCollapse = bootstrap.Collapse.getInstance(accordion_content) || new bootstrap.Collapse(accordion_content, {toggle: false});

    if (chat_mode === mode) {
      accordion_button.classList.remove("collapsed");
      accordion_button.setAttribute("aria-expanded", "true");
      bsCollapse.show();
    } else {
      accordion_button.classList.add("collapsed");
      accordion_button.setAttribute("aria-expanded", "false");
      bsCollapse.hide();
    }
  }
}

/**
 * Open librarian modal to a specific library/data source.
 * Called directly from onclick handlers in notifications.
 * If only libraryId provided, opens to library edit view.
 * If dataSourceId provided, opens to data source edit view.
 */
function openLibrarianModal(libraryId, dataSourceId) {
  if (!libraryId) return;

  const modalEl = document.getElementById('editLibrariesModal');
  const modalInner = document.getElementById('editLibrariesInner');
  if (!modalEl || !modalInner) {
    const params = new URLSearchParams();
    params.set('open_library', libraryId);
    if (dataSourceId) {
      params.set('open_data_source', dataSourceId);
    }
    window.location.href = `/chat/?${params.toString()}`;
    return;
  }

  // Switch to Q&A tab (normal UX path)
  handleModeChange('qa');

  // Keep form state in sync
  const qaLibrarySelect = document.getElementById('id_qa_library');
  if (qaLibrarySelect && libraryId) {
    qaLibrarySelect.value = libraryId;
  }

  // If dataSourceId provided, open data source view; otherwise open library view
  const targetUrl = dataSourceId
    ? `/librarian/modal/data_source/${dataSourceId}/edit/`
    : `/librarian/modal/library/${libraryId}/edit/`;

  // Load the modal content directly, then show the modal
  htmx.ajax('GET', targetUrl, {
    target: '#editLibrariesInner',
    swap: 'innerHTML'
  }).then(() => {
    if (modalEl) {
      const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl);
      bsModal.show();
    }
  });
}

/**
 * Open presets modal to browse presets.
 * Called directly from onclick handlers in notifications.
 * If presets modal elements are not available (not in AI Assistant), redirects to chat page.
 */
function openBrowsePresetsModal(presetId) {
  const modalEl = document.getElementById('presets-modal');
  const presetsButton = document.getElementById('presets-modal-button');
  if (!modalEl || !presetsButton) {
    // Not in AI Assistant - redirect to chat with open_preset param
    const params = new URLSearchParams();
    params.set('open_preset', presetId || '');
    window.location.href = `/chat/?${params.toString()}`;
    return;
  }

  // Click the browse presets button to load content and open modal
  presetsButton.click();
}

// Card links should change the mode dropdown and fire change event
function clickCard(mode) {
  toggleAriaSelected(mode);
  handleModeChange(mode);
  // Focus the chat input
  document.querySelector('#chat-prompt').focus();
}

function toggleAriaSelected(mode) {
  const cards = document.querySelectorAll('.nav-item button');
  cards.forEach(card => {
    card.classList.contains(`${mode}-option`) ? card.ariaSelected = true : card.ariaSelected = false;
  });
}

// Close the sidebars that are in "overlay mode" when clicking outside of them
document.querySelector("#chat-container").addEventListener('click', function (e) {
  let clicked_element = e.target;
  let left_sidebar = document.querySelector('#left-sidebar');
  let right_sidebar = document.querySelector('#right-sidebar');
  if (!left_sidebar || !right_sidebar) return;
  if (!(clicked_element.closest(".chat-sidebar-toggle") || left_sidebar.contains(clicked_element) || right_sidebar.contains(clicked_element))) {
    if (window.getComputedStyle(left_sidebar).position === "absolute" && !left_sidebar.classList.contains("hidden")) {
      closeSidebar("left-sidebar");
    }
    if (window.getComputedStyle(right_sidebar).position === "absolute" && !right_sidebar.classList.contains("hidden")) {
      closeSidebar("right-sidebar");
    }
  }
});

// Some resizing hacks to make the prompt form the same width as the messages
function resizePromptContainer() {
  let chatContainer = document.querySelector("#chat-container");
  let chatContentContainer = document.querySelector("#chat-content-container");
  let promptContainer = document.querySelector('#prompt-form-container');
  if (!promptContainer) return;
  // Align to the main chat content container (same layout layer as the prompt's inner container).
  let targetRect = (chatContentContainer || chatContainer).getBoundingClientRect();
  promptContainer.style.left = targetRect.left + "px";
  promptContainer.style.right = "auto";
  promptContainer.style.width = targetRect.width + "px";
  promptContainer.style.visibility = "visible";
  resizeOtherElements();
}
function showHideSidebars() {
  if (window.innerWidth < 1390) {
    closeSidebar("right-sidebar", false);
  } else {
    openSidebar("right-sidebar", false);
  }
  if (window.innerWidth <= 1270) {
    closeSidebar("left-sidebar", false);
  } else {
    openSidebar("left-sidebar", false);
  }
  resizePromptContainer();
}
window.addEventListener('resize', showHideSidebars);
// Initialize or re-initialize bootstrap tooltips
function initializeTooltips() {
  const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl, {delay: {show: 500, hide: 200}}));
  const presetActionButtons = document.querySelectorAll("div.preset-actions button");
  presetActionButtons.forEach(function (tooltipTriggerEl) {
    tooltipTriggerEl.addEventListener('click', function () {
      tooltipList.forEach(function (tooltip) {
        tooltip.hide();
      });
    });
  });
}

// On page load...
document.addEventListener("DOMContentLoaded", function () {
  showHideSidebars();

  // Markdown rendering
  document.querySelectorAll("div.message-text").forEach(function (element) {
    render_markdown(element);
    checkTruncation(element);
  });

  // Interactive UI setup — skip in readonly mode (no prompt, sidebars, accordion)
  const chatPrompt = document.querySelector('#chat-prompt');
  if (chatPrompt) {
    const mode = document.querySelector('#chat-outer').classList[0];
    updateAccordion(mode);
    updateQaSourceForms();
    updateTranslateForms();
    updatePlaceholder(mode);
    moveChatPromptCaretToEnd();
    // Set up padding/toolbar without causing CLS
    resizeOtherElements();
    // Only resize textarea if it has pre-filled content to avoid CLS
    if (chatPrompt.value.trim().length > 0) {
      resizeTextarea();
    }
    chatPrompt.focus();
  }
  const params = new URLSearchParams(window.location.search);
  const openLibrary = params.get('open_library');
  if (openLibrary) {
    const openDataSource = params.get('open_data_source');
    openLibrarianModal(openLibrary, openDataSource);
    params.delete('open_library');
    params.delete('open_data_source');
    const newQuery = params.toString();
    const newUrl = newQuery ? `${window.location.pathname}?${newQuery}` : window.location.pathname;
    history.replaceState(null, '', `${newUrl}${window.location.hash || ''}`);
  }
  const openPreset = params.get('open_preset');
  if (openPreset) {
    openBrowsePresetsModal(openPreset);
    params.delete('open_preset');
    const newQuery = params.toString();
    const newUrl = newQuery ? `${window.location.pathname}?${newQuery}` : window.location.pathname;
    history.replaceState(null, '', `${newUrl}${window.location.hash || ''}`);
  }
  if (document.querySelector("#no-messages-placeholder") === null) {
    setTimeout(scrollToBottom, 100);
  }
  // Initialize tooltips
  initializeTooltips();
  document.querySelectorAll('.chat-delete').forEach(button => {
    button.addEventListener('htmx:afterRequest', () => {
      deleteChatSection(button);
    });
  });
  // Load correct combine/separation info text depending on selected mode if applicable
  var select = document.getElementById("id_qa_mode");
  if (select) {
    switch_comb_sep_text(select);
  }
  // Hide RAG-only Q+A options in advanced modal on page load if applicable
  const ragOptions = document.querySelectorAll(".qa_rag_option");
  const qa_mode = document.getElementById("id_qa_mode");
  ragOptions.forEach(function (option) {
    if (qa_mode.value !== "rag") {
      option.style.display = "none";
    }
  });
  // If there's a search term in the URL, highlight it in the message and scroll to it
  try {
    const params = new URLSearchParams(window.location.search);
    const term = (params.get('search') || '').trim();
    const urlHash = window.location.hash;
    const anchor = urlHash && urlHash.startsWith('#message_') ? document.querySelector(urlHash) : null;

    if (anchor) {
      // If a search term exists, highlight only the occurrences inside the target message
      if (term) {
        expandAllMessages(chat_id);
        //set a timeout to allow message expansion to complete
        setTimeout(() => {
          const firstMark = highlightTermInElement(anchor, term);
          const scrollTarget = firstMark || anchor;
          scrollTarget.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 300);
      } else {
        // Fallback: just scroll to the message
        anchor.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
    }
  } catch (_) { /* noop */}
});

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

  // Set the clipboard data with cleaned HTML and plain text
  event.clipboardData.setData("text/html", tempDiv.innerHTML);
  event.clipboardData.setData("text/plain", tempDiv.innerText);
  event.preventDefault();
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
  document.querySelector("#chat-prompt").value = "";
  if (!chat_tour_in_progress) {
    document.querySelector("#chat-prompt").focus();
  }
  // Change height back to minimum
  document.querySelector("#chat-prompt").style.height = chatPromptMinHeight + "px";
  lastHeight = chatPromptMinHeight;
  scrollToBottom(false, true);
});
// When streaming response is updated
document.addEventListener("htmx:sseMessage", function (event) {
  if (!(event.target.id?.startsWith("response-"))) return;
  scheduleStreamingRender(event.target);
});
// When streaming response is finished
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id?.startsWith("message_"))) return;
  const messageId = event.detail.target.id.replace('message_', '');
  clearStreamingRenderState(messageId);
  const message_text = event.target.querySelector(".message-text");
  if (message_text) {
    render_markdown(message_text);
    checkTruncation(message_text);
  }
  scrollToBottom(false, false);
});
// Title updated
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.detail?.target?.id === "current-chat-title")) return;
  updatePageTitle();
});

document.addEventListener('DOMContentLoaded', schedulePendingTitleRefresh);
// Restart polling when the full sidebar list is reloaded (e.g. navigating to another chat)
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

// Sources modal setup
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id !== "sources-modal-inner") return;
  let targetElement = event.detail.target.querySelectorAll(".markdown-text");
  targetElement.forEach(function (element) {
    let decodedText = JSON.parse(element.dataset.md);
    let renderedMarkdown = md_with_html.render(decodedText);
    element.innerHTML = renderedMarkdown;
    element.querySelectorAll("a").forEach(function (link) {
      link.setAttribute("target", "_blank");
    });
  });
  // Hide #next-highlight if there are no "<mark>" elements
  if (event.detail.target.querySelector("mark") === null) {
    setTimeout(function () {
      // Check if the document.querySelector("#next-highlight") is visible
      if (document.querySelector("#next-highlight").classList.contains("d-none")) return;
      document.querySelector("#no-highlights").classList.remove("d-none");
      document.querySelector("#next-highlight").classList.add("d-none");
    }, 100);
  }
});

// reinitialize the delete button event listener after a chat is modified
document.addEventListener('htmx:afterRequest', function (event) {
  if (event.detail?.target?.id.startsWith('chat-list-item')) {
    const chat_id = event.detail.target.id.split("chat-list-item-")[1];
    const button = document.getElementById('delete-chat-' + chat_id);
    if (button) {
      button.addEventListener('htmx:afterRequest', () => {
        deleteChatSection(button);
      });
    }
  }
});

// scrolls to bottom if we rerun a prompt
document.addEventListener("htmx:afterSwap", function (event) {
  const target = event.detail?.target;
  const initiator = event.detail?.requestConfig?.elt;
  if (
    target?.classList?.contains("message-outer") &&
    initiator?.classList?.contains("rerun-prompt-button")
  ) {
    scrollToBottom(false, true);
  }
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
        return parsed.trim();
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
  return {
    html: messageTextClone.outerHTML,
    plain: messageTextClone.innerText,
  };
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
  document.querySelector("#" + sidebarID)?.classList.add("hidden");
  document.querySelector("#" + sidebarID + "-toggle")?.classList.remove("hidden");
  if (resizePrompt) {
    resizePromptContainer();
  }
}

function openSidebar(sidebarID, resizePrompt = true) {
  document.querySelector("#" + sidebarID)?.classList.remove("hidden");
  document.querySelector("#" + sidebarID + "-toggle")?.classList.add("hidden");
  if (resizePrompt) {
    resizePromptContainer();
  }
}

document.querySelector("#close-right-sidebar")
  ?.addEventListener("click", function () {closeSidebar("right-sidebar");});
document.querySelector("#right-sidebar-toggle")
  ?.addEventListener("click", function () {openSidebar("right-sidebar");});
document.querySelector("#close-left-sidebar")
  ?.addEventListener("click", function () {closeSidebar("left-sidebar");});
document.querySelector("#left-sidebar-toggle")
  ?.addEventListener("click", function () {openSidebar("left-sidebar");});

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

function updateQaModal() {
  const qa_mode = document.getElementById('id_qa_mode');
  toggleRagOptions(qa_mode);
  const qa_modal_elements = document.querySelectorAll('#advanced-qa-modal [data-inputname]');
  qa_modal_elements.forEach((modal_element) => {
    const hidden_input_name = modal_element.dataset.inputname;
    const hidden_field_element = document.querySelector(`input[name="${hidden_input_name}"]`);
    if (hidden_field_element) {
      if (modal_element.type === "checkbox") {
        modal_element.checked = hidden_field_element.value.toLowerCase() === "true";
        modal_element.value = modal_element.checked ? "true" : "false";
      } else {
        modal_element.value = hidden_field_element.value;
      }
    }
    toggleGranularOptions(document.getElementById('qa_granular_toggle-modal').value);
  });
};
function updateQaHiddenField(modal_element) {
  // console.log('Updating QA hidden field');
  // Dataset attributes are lowercased
  const hidden_field_name = modal_element.dataset.inputname;
  const hidden_field_element = document.querySelector(`input[name="${hidden_field_name}"]`);
  if (hidden_field_element) {
    hidden_field_element.value = modal_element.value;
    hidden_field_element.dispatchEvent(new Event('change'));
  }
};

function toggleGranularOptions(value) {
  var gran_slider = document.getElementById('qa_granularity_slider');

  if (value === 'True') {
    gran_slider.style.display = '';
  } else {
    gran_slider.style.display = 'none';
  }
}

function toggleRagOptions(elem) {
  var value = elem.value;
  var ragOptions = document.querySelectorAll('.qa_rag_option');

  ragOptions.forEach(function (option) {
    if (value !== 'rag') {
      option.style.display = 'none';
    } else {
      option.style.display = '';
    }
  });

  switch_comb_sep_text(elem);
}

function switch_comb_sep_text(elem) {
  var value = elem.value;
  var rag_string = elem.dataset.rag_string;
  var fulldoc_string = elem.dataset.fulldoc_string;
  var comb_sep = document.getElementById('comb_sep_info');

  if (value === 'rag') {
    comb_sep.setAttribute('data-bs-title', rag_string);
  }
  else {
    comb_sep.setAttribute('data-bs-title', fulldoc_string);
  }

  if (bootstrap.Tooltip.getInstance(comb_sep)) {
    bootstrap.Tooltip.getInstance(comb_sep).dispose();
  }
  new bootstrap.Tooltip(comb_sep, {delay: {show: 500, hide: 200}});
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

function expandAllSources(message_id, force_expand = false) {
  const sources = document.querySelectorAll(`#sources-${message_id}-accordion .accordion-item`);
  const expandAllLabel = document.querySelector(`#expand-all-label`);
  const collapseAllLabel = document.querySelector(`#collapse-all-label`);
  const expandAll = collapseAllLabel.classList.contains("d-none") || force_expand;
  sources.forEach(function (source) {
    const accordion = new bootstrap.Collapse(source.querySelector('.accordion-collapse'), {toggle: false});
    expandAll ? accordion.show() : accordion.hide();
  });
  if (expandAll) {
    expandAllLabel.classList.add("d-none");
    collapseAllLabel.classList.remove("d-none");
  } else {
    expandAllLabel.classList.remove("d-none");
    collapseAllLabel.classList.add("d-none");
  }
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
}


function nextSourceHighlight(message_id) {
  const highlights = document.querySelectorAll(`#sources-${message_id}-accordion mark`);
  if (highlights.length === 0) return;
  const collapseAllLabel = document.querySelector(`#collapse-all-label`);
  const needToExpand = collapseAllLabel.classList.contains("d-none");
  if (needToExpand) expandAllSources(message_id, true);

  // Next highlight is either the next after the current one or the first one
  const currentHighlight = document.querySelector(`#sources-${message_id}-accordion mark.current-highlight`);
  let nextHighlight = highlights[0];
  if (currentHighlight) {
    currentHighlight.classList.remove("current-highlight");
    const nextIndex = Array.from(highlights).indexOf(currentHighlight) + 1;
    if (nextIndex < highlights.length) {
      nextHighlight = highlights[nextIndex];
    }
  }

  // Wait for the sources to expand before scrolling to the first highlight
  setTimeout(() => {
    nextHighlight.classList.add("current-highlight");
    nextHighlight.scrollIntoView({behavior: "smooth", block: "center"});
  }, needToExpand ? 300 : 0);
}

function clearRemainingCostWarningButtons() {
  const warningButton = document.querySelector(".cost-warning-buttons");
  if (warningButton) {
    warningButton.remove();
  }
}

function initializeReasoningEffortToggle() {
  if (typeof initializeChatOptionsConditionalUI === 'function') {
    initializeChatOptionsConditionalUI();
  }
}

function afterAccordionSwap() {
  const accordion = document.getElementById('options-accordion');
  const presetLoaded = accordion.dataset.presetLoaded === "true";
  const swap = accordion.dataset.swap === "true";
  const triggerLibraryChange = accordion.dataset.triggerLibraryChange === "true";
  const mode = accordion.dataset.mode;
  const prompt = accordion.dataset.prompt;
  if (prompt) {
    document.querySelector('#chat-prompt').value = prompt;
  }

  if (presetLoaded) {
    handleModeChange(mode, null);
    const qa_mode = document.getElementById('id_qa_mode');
    switch_comb_sep_text(qa_mode);
    // Update forms with delay to ensure DOM is ready
    setTimeout(() => {
      updateQaSourceForms();
      updateTranslateForms();
      initializeReasoningEffortToggle();
    }, 100);
  } else if (swap) {
    // When swap=true, the accordion HTML is already in the correct state.
    // Only update the accordion if the mode is actually changing.
    const currentMode = document.querySelector('#chat-outer').classList[0];
    const modeChanged = mode !== currentMode;

    if (modeChanged) {
      // Mode changed: update accordion and all related state
      handleModeChange(mode, null);
    } else {
      // Mode unchanged: skip accordion manipulation, just sync hidden input
      document.querySelector('#id_mode').value = mode;
      triggerOptionSave();
    }

    // Update forms with delay to ensure DOM is ready
    setTimeout(() => {
      updateQaSourceForms();
      updateTranslateForms();
      initializeReasoningEffortToggle();
    }, 100);
  } else if (triggerLibraryChange) {
    // This function calls updateQaSourceForms, so no need to call it twice
    resetQaAutocompletes();
    initializeReasoningEffortToggle();
  } else {
    updateTranslateForms();
    updateQaSourceForms();
    initializeReasoningEffortToggle();
  }

  // Re-initialize tooltips after accordion swap
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

  const inLegacyLibrarianModal = target.closest('#editLibrariesModal.show #editLibrariesInner');

  if (!inLegacyLibrarianModal) {
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

async function navigationClickHandler(e) {
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
      // Trigger cancel buttons to stop client uploads
      cancelAllUploads();

      // Wait for in-progress uploads to reach a terminal state
      // (either removed, success or fail). This polls `.dff-file`
      // elements looking for any that are still pending.
      await waitForUploadsToFinish(5000);

      // Remove the navigation blocker and re-trigger the navigation
      setUploadsInProgress(false);
      target.click();
    }
  }
}

/**
 * Poll DOM until there are no in-progress .dff-file entries or timeout.
 * Returns true if uploads finished before timeout, false otherwise.
 */
function waitForUploadsToFinish(timeoutMs = 5000) {
  const start = Date.now();
  return new Promise((resolve) => {
    const check = () => {
      const files = Array.from(document.querySelectorAll('.dff-file'));
      const inProgress = files.some(f => !f.classList.contains('dff-upload-success') && !f.classList.contains('dff-upload-fail'));
      if (!inProgress) return resolve(true);
      if (Date.now() - start > timeoutMs) return resolve(false);
      setTimeout(check, 150);
    };
    check();
  });
}

/**
 * Trigger cancel action for all visible, enabled upload cancel buttons.
 * The django-file-form renderer adds `.dff-cancel` buttons for in-progress uploads
 * which call the library's abort logic. Clicking those buttons will stop client
 * side uploads.
 */
function cancelAllUploads() {
  try {
    const cancelButtons = Array.from(document.querySelectorAll('.dff-cancel'));
    cancelButtons.forEach(btn => {
      if (!btn.classList.contains('dff-disabled') && btn.offsetParent !== null) {
        btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
      }
    });
  } catch (err) {
    // Fail silently - we don't want navigation blocked by JS errors here
    console.error('Error cancelling uploads', err);
  }
}

function beforeUnloadHandler(event) {
  event.preventDefault();
  try {
    event.returnValue = CANCEL_UPLOAD_WARNING;
  } catch (e) {
    // ignore
  }
  return CANCEL_UPLOAD_WARNING;
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
  if (messageId) {
    clearStreamingRenderState(messageId);
  }

  // If we have a message ID, check if the server already completed the response
  if (messageId) {
    recoverCompletedMessage(messageId).then((recovered) => {
      if (!recovered && element.isConnected) {
        showSSEErrorUI(element, messageId);
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
  const errorText = element.dataset.errorText || 'Connection lost. The response may be incomplete.';
  const retryText = element.dataset.retryText || 'Retry';
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
  hideStreamingStopButton();
}

/**
 * Retry an SSE connection after an error.
 * Re-creates the SSE element to trigger a fresh connection.
 */
function retrySSEConnection(button, messageId, sseConnectUrl) {
  const responseDiv = document.getElementById(`response-${messageId}`);
  if (!responseDiv) return;
  clearStreamingRenderState(messageId);

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

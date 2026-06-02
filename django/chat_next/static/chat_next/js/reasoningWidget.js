// Processing steps widget and approval workflow for chat_next.

// Global state for reasoning widget - tracks expansion state per message
const reasoningState = new Map();

function createReasoningState(isFinished = false) {
  return {
    expanded: false,
    isFinished,
    hasText: false,
    handledApprovalRequestId: null,
    previousSteps: [],
    pendingStreamingRender: null,
    streamingRenderRaf: null,
    reviewedApprovalRequests: {},
    approvalGateCleanup: null,
  };
}

function getOrCreateReasoningState(messageId, isFinished = false) {
  let state = reasoningState.get(messageId);
  if (!state) {
    state = createReasoningState(isFinished);
    reasoningState.set(messageId, state);
  } else {
    if (!Array.isArray(state.previousSteps)) {
      state.previousSteps = [];
    }
    if (!Object.prototype.hasOwnProperty.call(state, 'handledApprovalRequestId')) {
      state.handledApprovalRequestId = null;
    }
    if (!Object.prototype.hasOwnProperty.call(state, 'pendingStreamingRender')) {
      state.pendingStreamingRender = null;
    }
    if (!Object.prototype.hasOwnProperty.call(state, 'streamingRenderRaf')) {
      state.streamingRenderRaf = null;
    }
    if (!Object.prototype.hasOwnProperty.call(state, 'reviewedApprovalRequests')) {
      state.reviewedApprovalRequests = {};
    }
    if (!Object.prototype.hasOwnProperty.call(state, 'approvalGateCleanup')) {
      state.approvalGateCleanup = null;
    }
  }
  return state;
}

function getReasoningStepTextFingerprint(text) {
  const value = typeof text === 'string' ? text : '';
  if (value.length <= 512) {
    return value;
  }

  return `${value.length}:${value.slice(0, 160)}:${value.slice(-160)}`;
}

function getReasoningStepSignature(step, state, index) {
  return [
    index,
    step.title || '',
    step.title_html || '',
    step.status || '',
    step.tool_type || '',
    step.approval_status || '',
    step.approval_request_id || '',
    step.is_approval_request ? '1' : '0',
    step.allow_auto_approve ? '1' : '0',
    step.estimated_cost || '',
    step.output ? '1' : '0',
    step.pii_flagged ? '1' : '0',
    state.isFinished ? '1' : '0',
    state.approvalSubmitted || '',
    state.submittedRequestId || '',
    getReasoningStepTextFingerprint(step.details),
    getReasoningStepTextFingerprint(step.approval_input_html),
    getReasoningStepTextFingerprint(JSON.stringify(step.risk_review || {})),
  ].join('||');
}

function getReasoningContentSignature(steps, state, isReasoning, translationOptions = null) {
  const translationSignature = translationOptions
    ? [
      translationOptions.canTranslateProcessingSteps ? '1' : '0',
      translationOptions.translateUrl || '',
      translationOptions.translationError || '',
    ].join('||')
    : '';

  return [
    isReasoning ? '1' : '0',
    state.isFinished ? '1' : '0',
    state.hasText ? '1' : '0',
    state.approvalSubmitted || '',
    state.submittedRequestId || '',
    translationSignature,
    ...steps.map((step, index) => getReasoningStepSignature(step, state, index))
  ].join('§');
}

function clearApprovalInteractionFocus(sourceEl) {
  if (!sourceEl) return;

  if (typeof sourceEl.blur === 'function') {
    sourceEl.blur();
  }

  const interactionRoot = sourceEl.closest('.message-outer') || sourceEl.closest('.reasoning-widget');
  const activeElement = document.activeElement;
  if (
    interactionRoot
    && activeElement
    && activeElement !== document.body
    && interactionRoot.contains(activeElement)
    && typeof activeElement.blur === 'function'
  ) {
    activeElement.blur();
  }
}

function getLatestActionableApprovalStep(steps, state) {
  if (!Array.isArray(steps)) return null;

  for (let i = steps.length - 1; i >= 0; i--) {
    const step = steps[i];
    if (!(step && step.is_approval_request && step.approval_request_id)) {
      continue;
    }

    if (step.approval_status === 'approved' || step.approval_status === 'denied') {
      continue;
    }

    if (
      state.approvalSubmitted
      && state.submittedRequestId
      && step.approval_request_id === state.submittedRequestId
    ) {
      continue;
    }

    return step;
  }

  return null;
}

function isPendingApprovalStep(step) {
  return Boolean(
    step
    && (
      step.status === 'waiting_approval'
      || (
        step.is_approval_request
        && step.approval_status !== 'approved'
        && step.approval_status !== 'denied'
      )
    )
  );
}

function getPendingApprovalSteps(steps, state) {
  if (!Array.isArray(steps)) {
    return [];
  }

  return steps.filter((step) => {
    if (!isPendingApprovalStep(step)) {
      return false;
    }

    if (
      state.approvalSubmitted
      && state.submittedRequestId
      && step.approval_request_id
      && step.approval_request_id === state.submittedRequestId
    ) {
      return false;
    }

    return true;
  });
}

function getLatestChatMessageElement() {
  const messages = document.querySelectorAll('#messages-container .message-outer');
  return messages[messages.length - 1] || null;
}

function maybeFocusLatestApprovalButton(widget, steps, state) {
  const approvalStep = getLatestActionableApprovalStep(steps, state);
  if (!approvalStep) {
    return;
  }

  const requestId = approvalStep.approval_request_id;
  if (!requestId) {
    return;
  }

  const messageId = widget.dataset.messageId;
  const messageEl = messageId ? document.getElementById(`message_${messageId}`) : null;
  const latestMessageEl = getLatestChatMessageElement();
  if (!messageEl || !latestMessageEl || messageEl !== latestMessageEl) {
    return;
  }

  if (typeof window.chatNextShouldAutoScroll === 'function' && !window.chatNextShouldAutoScroll()) {
    return;
  }

  const approveButton = Array.from(widget.querySelectorAll('.reasoning-approve-btn'))
    .find((button) => button.dataset.approvalRequestId === requestId);
  if (!approveButton) {
    return;
  }

  const activeElement = document.activeElement;
  const shouldRestoreSameRequestFocus = state.handledApprovalRequestId === requestId
    && (
      !activeElement
      || activeElement === document.body
      || widget.contains(activeElement)
    );
  if (state.handledApprovalRequestId === requestId && !shouldRestoreSameRequestFocus) {
    return;
  }

  requestAnimationFrame(() => {
    if (!approveButton.isConnected) {
      return;
    }

    if (typeof window.chatNextShouldAutoScroll === 'function' && !window.chatNextShouldAutoScroll()) {
      return;
    }

    const focusApproveButton = function () {
      try {
        approveButton.focus({preventScroll: true});
      } catch (_) {
        approveButton.focus();
      }

      approveButton.classList.add('reasoning-focus-visible');
      requestAnimationFrame(() => {
        approveButton.classList.remove('reasoning-focus-visible');
      });
    };

    if (typeof window.runWithProgrammaticChatNextScroll === 'function') {
      window.runWithProgrammaticChatNextScroll(focusApproveButton);
    } else {
      focusApproveButton();
    }

    state.handledApprovalRequestId = requestId;

    window.requestChatNextScrollToBottom?.();
  });
}

const approvalUiDatasetKeys = [
  'actionRequiredTitle',
  'externalApprovalWarningLabel',
  'externalApprovalWarningText',
  'externalApprovalWarningTextPrefix',
  'externalApprovalPolicyLinkText',
  'externalApprovalPolicyLinkUrl',
  'externalApprovalWarningTextSuffix',
  'externalApprovalApproveText',
  'externalApprovalDenyText',
  'externalApprovalEstimatedCostText',
];

function syncApprovalUiTextDataset(widget, sourceEl) {
  if (!widget || !sourceEl) return;

  approvalUiDatasetKeys.forEach((key) => {
    const value = sourceEl.dataset?.[key];
    if (typeof value === 'string' && value.length > 0) {
      widget.dataset[key] = value;
    }
  });
}

function getApprovalUiText(messageId) {
  const widget = document.querySelector(`.reasoning-widget[data-message-id="${messageId}"]`);
  return {
    warningLabel: widget?.dataset.externalApprovalWarningLabel || t('external_approval_warning_label', 'Warning'),
    warningText: widget?.dataset.externalApprovalWarningText || t('external_approval_warning_text', 'Sending a request to a database outside the Government of Canada where the request may be viewed. Outgoing data must not be Protected, Classified or privileged information according to Justice’s Handling and Safeguarding Sensitive Information.'),
    warningTextPrefix: widget?.dataset.externalApprovalWarningTextPrefix || t('external_approval_warning_text_prefix', 'Sending a request to a database outside the Government of Canada where the request may be viewed. Outgoing data must not be Protected, Classified or privileged information according to Justice’s'),
    warningPolicyLinkText: widget?.dataset.externalApprovalPolicyLinkText || t('external_approval_policy_link_text', 'Handling and Safeguarding Sensitive Information'),
    warningPolicyLinkUrl: widget?.dataset.externalApprovalPolicyLinkUrl || '',
    warningTextSuffix: widget?.dataset.externalApprovalWarningTextSuffix || '.',
    approveText: widget?.dataset.externalApprovalApproveText || t('external_approval_approve', 'Query contains no Protected, Classified or privileged information'),
    denyText: widget?.dataset.externalApprovalDenyText || t('deny', 'Deny'),
    estimatedCostText: widget?.dataset.externalApprovalEstimatedCostText || t('estimated_cost', 'Estimated cost'),
  };
}

function getActionRequiredTitle(widget) {
  return widget?.dataset.actionRequiredTitle || t('action_required_title', '⚠️ Action Required');
}

function getRiskReview(step) {
  return step && typeof step.risk_review === 'object' && step.risk_review !== null
    ? step.risk_review
    : null;
}

function formatRiskReviewSource(source) {
  if (source === 'heuristic') {
    return t('local_safety_review', 'local safety review');
  }

  if (source === 'azure_language') {
    return t('azure_language_pii_review', 'Azure Language PII review');
  }

  return String(source || '').replace(/_/g, ' ');
}

function buildRiskReviewNoticeHtml(step) {
  const riskReview = getRiskReview(step);
  if (!riskReview || !riskReview.flagged) {
    return '';
  }

  const piiCategories = Array.isArray(riskReview.pii_entity_categories)
    ? riskReview.pii_entity_categories.filter(Boolean)
    : [];
  const summaryItems = Array.isArray(riskReview.summary_items)
    ? riskReview.summary_items.filter(Boolean)
    : [];
  const noticeItems = piiCategories.length ? piiCategories : summaryItems;
  const summaryText = noticeItems.length
    ? escapeHtml(noticeItems.join(', '))
    : escapeHtml(t('sensitive_content_review_recommended', 'Please confirm the outbound request is sanitized, minimal, and unclassified.'));

  return `
    <div class="small text-warning-emphasis mb-0">
      <span class="fw-semibold">${escapeHtml(t('sensitive_content_detected', 'Potentially sensitive content detected'))}:</span>
      ${summaryText}
    </div>
  `;
}

function getApprovalFooterSummaryText(approvalStep, pendingSteps) {
  const pendingCount = pendingSteps.length || 1;
  const requestWord = pendingCount === 1
    ? t('pending_request_singular', 'request')
    : t('pending_request_plural', 'requests');
  const serviceNames = Array.isArray(approvalStep?.external_service_names)
    ? approvalStep.external_service_names.filter(Boolean)
    : [];

  let summaryText = `${t('review_pending_requests', 'Review')} ${pendingCount} ${requestWord}`;
  if (serviceNames.length === 1) {
    summaryText += ` ${t('for_service', 'for')} ${serviceNames[0]}`;
  } else if (serviceNames.length > 1) {
    summaryText += ` ${t('for_services', 'for')} ${serviceNames.join(', ')}`;
  }

  summaryText += '.';
  return summaryText;
}

function buildApprovalFooterHtml(approvalStep, pendingSteps, messageId) {
  if (!(approvalStep && approvalStep.approval_request_id)) {
    return '';
  }

  const approvalUiText = getApprovalUiText(messageId);
  const requiresExternalApprovalWarning = Boolean(approvalStep.approval_requires_external_warning);
  const hasFlaggedPendingRequest = pendingSteps.some((step) => {
    if (!step) {
      return false;
    }

    return Boolean(
      step.pii_flagged
      || step.risk_review?.flagged
    );
  });
  const approveButtonClass = hasFlaggedPendingRequest ? 'btn-warning' : 'btn-success';
  const approveButtonText = requiresExternalApprovalWarning
    ? approvalUiText.approveText
    : (
      approvalStep.estimated_cost
        ? `${t('approve_at_cost', 'Continue at estimated cost')} $${approvalStep.estimated_cost}`
        : `${t('approve', 'Approve')}`
    );
  const policyLinkHtml = approvalUiText.warningPolicyLinkUrl
    ? `<a href="${escapeHtml(approvalUiText.warningPolicyLinkUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(approvalUiText.warningPolicyLinkText)}</a>`
    : escapeHtml(approvalUiText.warningPolicyLinkText);

  const warningTextHtml = approvalUiText.warningPolicyLinkText
    ? `${escapeHtml(approvalUiText.warningTextPrefix)} ${policyLinkHtml}${escapeHtml(approvalUiText.warningTextSuffix || '')}`
    : escapeHtml(approvalUiText.warningText);

  const preButtonWarningHtml = requiresExternalApprovalWarning
    ? `
      <div class="small text-muted mb-2">
        <span class="fw-semibold">${escapeHtml(approvalUiText.warningLabel)}:</span>
        ${warningTextHtml}
      </div>
      ${approvalStep.estimated_cost ? `<div class="small text-warning-emphasis mb-2">${escapeHtml(approvalUiText.estimatedCostText)}: $${escapeHtml(String(approvalStep.estimated_cost))}</div>` : ''}
    `
    : '';

  return `
    <div class="reasoning-approval-footer">
      <div class="reasoning-approval-footer-summary small text-muted mb-2">
        ${escapeHtml(getApprovalFooterSummaryText(approvalStep, pendingSteps))}
      </div>
      ${preButtonWarningHtml}
      <div class="reasoning-approval-scroll-hint small text-muted mb-2" hidden>
        ${escapeHtml(t('scroll_to_unlock_approval', 'Scroll to the end of the review list to enable approval.'))}
      </div>
      <div class="d-flex flex-wrap gap-1 reasoning-approval-actions">
        <button type="button"
                class="btn btn-sm ${approveButtonClass} reasoning-approve-btn me-1"
                data-approval-request-id="${approvalStep.approval_request_id}"
                onclick="submitApproval('${approvalStep.approval_request_id}', true, this)">
          <i class="bi bi-check-lg"></i> ${escapeHtml(approveButtonText)}
        </button>
        <button type="button"
                class="btn btn-sm btn-danger"
                onclick="submitApproval('${approvalStep.approval_request_id}', false, this)">
          <i class="bi bi-x-lg"></i> ${escapeHtml(requiresExternalApprovalWarning ? approvalUiText.denyText : t('deny', 'Deny'))}
        </button>
      </div>
    </div>
  `;
}

function getScrollableAncestor(element) {
  let current = element?.parentElement || null;
  while (current) {
    const style = window.getComputedStyle(current);
    const canScrollY = /(auto|scroll)/.test(style.overflowY) && current.scrollHeight > current.clientHeight;
    if (canScrollY) {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

function syncApprovalReviewPaneLayout(reviewEl, actionsEl) {
  if (!(reviewEl && actionsEl)) {
    return;
  }

  reviewEl.style.maxHeight = '';

  const scrollContainer = getScrollableAncestor(reviewEl);
  if (!scrollContainer) {
    return;
  }

  const containerHeight = scrollContainer.clientHeight;
  if (!containerHeight) {
    return;
  }

  const containerRect = scrollContainer.getBoundingClientRect();
  const reviewRect = reviewEl.getBoundingClientRect();
  const reviewTopInContent = scrollContainer.scrollTop + (reviewRect.top - containerRect.top);
  const contentBelowReview = scrollContainer.scrollHeight - reviewTopInContent - reviewEl.offsetHeight;
  const computedMaxHeight = Number.parseFloat(window.getComputedStyle(reviewEl).maxHeight);
  const naturalHeight = reviewEl.scrollHeight;
  const topInset = 16;
  const bottomInset = 16;
  const availableHeight = containerHeight - topInset - bottomInset - contentBelowReview;

  let targetHeight = naturalHeight;
  if (Number.isFinite(computedMaxHeight) && computedMaxHeight > 0) {
    targetHeight = Math.min(targetHeight, computedMaxHeight);
  }
  if (Number.isFinite(availableHeight) && availableHeight > 0) {
    targetHeight = Math.min(targetHeight, availableHeight);
  }

  if (!Number.isFinite(targetHeight) || targetHeight <= 0) {
    return;
  }

  reviewEl.style.maxHeight = `${Math.floor(targetHeight)}px`;
}

function teardownApprovalReviewGate(widget, state) {
  if (state.approvalGateCleanup) {
    state.approvalGateCleanup();
    state.approvalGateCleanup = null;
  }

  const reviewEl = widget?.querySelector('.reasoning-pending-approval-list');
  const actionsEl = widget?.querySelector('.reasoning-widget-actions');
  reviewEl?.classList.remove('pending-approval', 'approval-review-unseen');
  actionsEl?.classList.remove('approval-footer');
}

function setupApprovalReviewGate(widget, reviewEl, actionsEl, approvalStep, state) {
  teardownApprovalReviewGate(widget, state);

  if (!(approvalStep && approvalStep.approval_request_id && reviewEl && actionsEl)) {
    return;
  }

  const approveButton = actionsEl.querySelector('.reasoning-approve-btn');
  const scrollHint = actionsEl.querySelector('.reasoning-approval-scroll-hint');
  if (!approveButton) {
    return;
  }

  const requestId = approvalStep.approval_request_id;
  reviewEl.classList.add('pending-approval');
  actionsEl.classList.add('approval-footer');

  const markReviewed = function () {
    state.reviewedApprovalRequests[requestId] = true;
    approveButton.disabled = false;
    if (scrollHint) {
      scrollHint.hidden = true;
    }
    reviewEl.classList.remove('approval-review-unseen');
  };

  const evaluateGate = function () {
    if (!reviewEl.isConnected || !approveButton.isConnected) {
      return;
    }

    const hasOverflow = reviewEl.scrollHeight > reviewEl.clientHeight + 4;
    if (!hasOverflow || state.reviewedApprovalRequests[requestId]) {
      markReviewed();
      return;
    }

    const reachedBottom = reviewEl.scrollTop + reviewEl.clientHeight >= reviewEl.scrollHeight - 4;
    if (reachedBottom) {
      markReviewed();
      return;
    }

    approveButton.disabled = true;
    if (scrollHint) {
      scrollHint.hidden = false;
    }
    reviewEl.classList.add('approval-review-unseen');
  };

  const handleScroll = function () {
    evaluateGate();
  };

  const handleViewportResize = function () {
    syncApprovalReviewPaneLayout(reviewEl, actionsEl);
    evaluateGate();
  };

  reviewEl.addEventListener('scroll', handleScroll, {passive: true});
  window.addEventListener('resize', handleViewportResize);
  window.visualViewport?.addEventListener('resize', handleViewportResize);
  state.approvalGateCleanup = function () {
    reviewEl.removeEventListener('scroll', handleScroll);
    window.removeEventListener('resize', handleViewportResize);
    window.visualViewport?.removeEventListener('resize', handleViewportResize);
    reviewEl.classList.remove('pending-approval', 'approval-review-unseen');
    reviewEl.style.maxHeight = '';
    actionsEl.classList.remove('approval-footer');
  };

  syncApprovalReviewPaneLayout(reviewEl, actionsEl);
  evaluateGate();
}

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
  const state = getOrCreateReasoningState(messageId, false);
  state.pendingStreamingRender = {container, dataEl, messageId};

  if (state.streamingRenderRaf) {
    return;
  }

  state.streamingRenderRaf = requestAnimationFrame(() => {
    state.streamingRenderRaf = null;
    const pendingRender = state.pendingStreamingRender;
    state.pendingStreamingRender = null;

    if (!pendingRender?.container?.isConnected || !pendingRender?.dataEl?.isConnected) {
      return;
    }

    renderReasoningFromDataNow(
      pendingRender.container,
      pendingRender.dataEl,
      pendingRender.messageId,
    );
  });
}

function renderReasoningFromDataNow(container, dataEl, messageId) {
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
  const thinkingText = container.dataset.thinkingText || t('thinking', "Thinking...");
  const showReasoningText = container.dataset.showReasoningText || t('show_processing_steps', "Show processing steps");
  const generatingText = container.dataset.generatingText || t('generating_final_response', "Generating final response");
  const reasoningStepsText = container.dataset.reasoningStepsText || t('processing_steps', "Processing steps");
  const emptyReasoningText = container.dataset.emptyReasoningText || t('processing_steps_empty', "If the model provides reasoning steps, they will be shown here shortly.");

  const isReasoning = dataEl.dataset.isReasoning === "true";

  // Check if response text has started streaming by looking for .markdown-text element
  // When LLM starts generating text, it wraps it in a .markdown-text div
  const responseEl = document.getElementById(`response-${messageId}`);
  const hasResponseText = responseEl && responseEl.querySelector('.markdown-text') !== null;

  // Get or initialize state
  // During streaming, isFinished stays false
  // hasText tracks whether response text has started (for "Generating final response" state)
  // previousSteps tracks the last non-empty steps to avoid flickering on empty updates
  // When streaming ends, renderReasoningFromSaved will be called which sets isFinished=true
  const state = getOrCreateReasoningState(messageId, false);
  if (reasoningState.has(messageId)) {
    // Approval resume reuses the same widget state that was previously rendered
    // as a saved message. Force it back into streaming mode so code blocks keep
    // the streaming treatment while updates are still arriving.
    state.isFinished = false;
    if (!hasResponseText) {
      state.hasText = false;
    }
  }

  // Update hasText state - once text starts, it stays true until finished
  if (hasResponseText) {
    state.hasText = true;
  }

  // If new steps are empty but we have previous steps, keep showing previous steps
  // This prevents the widget from flickering to empty during approval resume
  if (steps.length === 0 && state.previousSteps && state.previousSteps.length > 0) {
    steps = state.previousSteps;
  } else if (steps.length > 0) {
    // Update previous steps cache when we have actual steps
    state.previousSteps = steps;
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

  syncApprovalUiTextDataset(widget, container);

  // Update widget content
  updateReasoningContent(widget, steps, isReasoning, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText);
}

/**
 * Handle reasoning for saved/loaded messages.
 * Data comes from reasoning-summary div attributes or script tag.
 */
function renderReasoningFromSaved(element, reasoningSummary) {
  const messageId = reasoningSummary.id.replace('reasoning-', '');

  // Get or initialize state early so we can reuse previous steps during transient empty updates.
  const state = getOrCreateReasoningState(messageId, true);
  if (state.streamingRenderRaf) {
    cancelAnimationFrame(state.streamingRenderRaf);
    state.streamingRenderRaf = null;
    state.pendingStreamingRender = null;
  }

  // Saved messages are finished; preserve any previously cached steps.
  state.isFinished = true;

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

  // If this update is temporarily empty (common during approval transition),
  // keep showing the last known non-empty steps to avoid flicker.
  if (steps.length === 0 && state.previousSteps && state.previousSteps.length > 0) {
    steps = state.previousSteps;
  } else if (steps.length > 0) {
    state.previousSteps = steps;
  }

  // Hide if no data
  if (steps.length === 0) {
    reasoningSummary.innerHTML = "";
    return;
  }

  const thinkingText = reasoningSummary.dataset.thinkingText || t('thinking', "Thinking...");
  const showReasoningText = reasoningSummary.dataset.showReasoningText || t('show_processing_steps', "Show processing steps");
  const reasoningStepsText = reasoningSummary.dataset.reasoningStepsText || t('processing_steps', "Processing steps");
  const emptyReasoningText = reasoningSummary.dataset.emptyReasoningText || t('processing_steps_empty', "If the model provides reasoning steps, they will be shown here shortly.");
  const translationOptions = {
    canTranslateProcessingSteps: reasoningSummary.dataset.canTranslateReasoningSteps === 'true',
    translateUrl: reasoningSummary.dataset.translateProcessingStepsUrl || '',
    translateText: reasoningSummary.dataset.translateProcessingStepsText || t('translate_reasoning_steps', 'Translate reasoning steps to French'),
    translatingText: reasoningSummary.dataset.translatingProcessingStepsText || t('translating_reasoning_steps', 'Translating reasoning steps to French...'),
    translateTitle: reasoningSummary.dataset.translateProcessingStepsTitle || t('translate_reasoning_steps_title', 'Translate reasoning steps to French'),
    translationError: reasoningSummary.dataset.translationError || ''
  };

  // Check if widget exists
  let widget = reasoningSummary.querySelector('.reasoning-widget');
  if (!widget) {
    widget = createReasoningWidget(messageId, state);
    reasoningSummary.appendChild(widget);
  }

  syncApprovalUiTextDataset(widget, reasoningSummary);

  const generatingText = reasoningSummary.dataset.generatingText || t('generating_final_response', "Generating final response");

  // Update widget content
  updateReasoningContent(widget, steps, false, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText, translationOptions);
}

/**
 * Create the reasoning widget DOM structure.
 */
function createReasoningWidget(messageId, state) {
  const widget = document.createElement('div');
  widget.className = 'reasoning-widget';
  widget.dataset.messageId = messageId;  // Store message ID for approval buttons

  const header = document.createElement('div');
  header.className = 'reasoning-header';
  header.innerHTML = `
    <button type="button" class="reasoning-header-toggle${state.expanded ? '' : ' collapsed'}">
      <span class="reasoning-icon"><i class="bi bi-chat-dots"></i></span>
      <span class="reasoning-title"></span>
    </button>
    <div class="reasoning-header-actions"></div>
    <button type="button" class="reasoning-header-caret${state.expanded ? '' : ' collapsed'}" aria-label="Toggle processing steps">
      <span class="reasoning-toggle"><i class="bi bi-chevron-right"></i></span>
    </button>
  `;

  const content = document.createElement('div');
  content.className = 'reasoning-content' + (state.expanded ? '' : ' collapsed');
  content.innerHTML = '<div class="reasoning-steps-list"></div><div class="reasoning-pending-approval-list"></div><div class="reasoning-widget-actions"></div>';

  // Toggle handler
  const headerToggle = header.querySelector('.reasoning-header-toggle');
  const headerCaret = header.querySelector('.reasoning-header-caret');
  const toggleExpanded = function (e) {
    e.preventDefault();
    state.expanded = !state.expanded;
    headerToggle.classList.toggle('collapsed', !state.expanded);
    headerCaret.classList.toggle('collapsed', !state.expanded);
    content.classList.toggle('collapsed', !state.expanded);
    // Update header text based on new state
    // Pass false for allStepsComplete since toggling happens after streaming
    const titleEl = headerToggle.querySelector('.reasoning-title');
    if (titleEl) {
      titleEl.textContent = getHeaderText(state, header.dataset.lastStepTitle,
        header.dataset.lastStepToolType,
        header.dataset.thinkingText, header.dataset.showReasoningText, header.dataset.generatingText, header.dataset.reasoningStepsText, false, header.dataset.hasApproval === 'true');
    }
    renderReasoningHeaderActions(widget, state, state.translationOptions);
    if (typeof resizeOtherElements === 'function') {
      // Keep chat layout padding aligned with the expanded/collapsed widget.
      setTimeout(resizeOtherElements, 300);
      resizeOtherElements();
    }
    window.requestChatNextScrollToBottom?.();
  };

  headerToggle.addEventListener('click', toggleExpanded);
  headerCaret.addEventListener('click', toggleExpanded);

  widget.appendChild(header);
  widget.appendChild(content);

  return widget;
}

function buildReasoningStepRenderData(step, index, state, messageId) {
  const riskWarningHtml = buildRiskReviewNoticeHtml(step);
  const stepIsPendingApproval = isPendingApprovalStep(step);
  const detailsHtml = step.approval_input_html && stepIsPendingApproval
    ? step.approval_input_html
    : (
      step.details
        ? renderCodeBlocks(step.details, state.isFinished ? {} : {
          disableSyntaxHighlight: true,
          truncateStreamingCode: true,
          truncatedStreamingNotice: t(
            'streaming_processing_steps_code_trimmed',
            'Large code preview trimmed during streaming to keep the browser responsive. Full code will appear when the response finishes.'
          )
        })
        : ''
    );

  // Determine approval UI: buttons (pending), status badge (resolved), or nothing
  let approvalHtml = '';
  if (step.approval_status === 'approved') {
    approvalHtml = `
      <div class="reasoning-step-approval">
        <span class="text-success small"><i class="bi bi-check-circle-fill"></i> ${t('approved_by_user', 'Approved by user')}</span>
      </div>
    `;
  } else if (step.approval_status === 'denied') {
    approvalHtml = `
      <div class="reasoning-step-approval">
        <span class="text-danger small"><i class="bi bi-x-circle-fill"></i> ${t('denied_by_user', 'Denied by user')}</span>
      </div>
    `;
  } else if (step.is_approval_request) {
    // Check if this approval was just submitted (tracked in state)
    if (state.approvalSubmitted && state.submittedRequestId === step.approval_request_id) {
      // Show the approval status instead of buttons
      const statusClass = state.approvalSubmitted === 'approved' ? 'text-success' : 'text-danger';
      const statusIcon = state.approvalSubmitted === 'approved' ? 'bi-check-circle-fill' : 'bi-x-circle-fill';
      const statusText = state.approvalSubmitted === 'approved'
        ? t('approved_by_user', 'Approved by user')
        : t('denied_by_user', 'Denied by user');
      approvalHtml = `
        <div class="reasoning-step-approval">
          <span class="${statusClass} small"><i class="bi ${statusIcon}"></i> ${statusText}</span>
        </div>
      `;
    }
  }

  let outputHtml = '';
  if (step.output && state.isFinished) {
    outputHtml = `
        <button type="button"
                class="btn btn-link btn-sm p-0 mt-1 text-muted"
                style="font-size:0.8125rem"
                onclick="openToolOutputModal(${messageId}, ${index})">
          <i class="bi bi-eye me-1"></i>${t('show_output', 'Show output')}
        </button>
      `;
  }

  const detailsClasses = state.isFinished
    ? 'reasoning-step-details'
    : 'reasoning-step-details streaming';

  const isRiskFlagged = Boolean(step.pii_flagged || getRiskReview(step)?.flagged);
  const stepTitleMarkup = step.title_html || escapeHtml(step.title);

  return {
    signature: getReasoningStepSignature(step, state, index),
    className: `reasoning-step${stepIsPendingApproval || isRiskFlagged ? ' warning' : ''}`,
    html: `
      <div class="reasoning-step-title">
        <span class="reasoning-step-number">${index + 1}.</span>
        <span class="reasoning-step-text">${stepTitleMarkup}</span>
      </div>
      ${detailsHtml ? `<div class="${detailsClasses}">${detailsHtml}</div>` : ''}
      ${riskWarningHtml}
      ${approvalHtml}
      ${outputHtml}
    `
  };
}

function syncReasoningStepsList(stepsList, stepEntries, state, messageId, emptyReasoningText, showEmptyState = false) {
  if (stepEntries.length === 0) {
    if (!showEmptyState) {
      if (stepsList.innerHTML !== '') {
        stepsList.innerHTML = '';
      }
      delete stepsList.dataset.renderMode;
      return;
    }

    const emptyHtml = `<div class="reasoning-step-empty text-muted fst-italic">${escapeHtml(emptyReasoningText)}</div>`;
    if (stepsList.dataset.renderMode !== 'empty' || stepsList.innerHTML !== emptyHtml) {
      stepsList.innerHTML = emptyHtml;
      stepsList.dataset.renderMode = 'empty';
    }
    return;
  }

  if (stepsList.dataset.renderMode !== 'steps') {
    stepsList.innerHTML = '';
    stepsList.dataset.renderMode = 'steps';
  }

  for (let i = 0; i < stepEntries.length; i++) {
    const stepEntry = stepEntries[i];
    const renderData = buildReasoningStepRenderData(stepEntry.step, stepEntry.index, state, messageId);
    let stepEl = stepsList.children[i];

    if (!stepEl) {
      stepEl = document.createElement('div');
      stepsList.appendChild(stepEl);
    }

    if (stepEl.dataset.renderSignature !== renderData.signature) {
      stepEl.className = renderData.className;
      stepEl.innerHTML = renderData.html;
      stepEl.dataset.renderSignature = renderData.signature;
    } else if (stepEl.className !== renderData.className) {
      stepEl.className = renderData.className;
    }
  }

  while (stepsList.children.length > stepEntries.length) {
    stepsList.removeChild(stepsList.lastElementChild);
  }
}

/**
 * Update the reasoning widget content without replacing the widget itself.
 */
function updateReasoningContent(widget, steps, isReasoning, state, thinkingText, showReasoningText, generatingText, reasoningStepsText, emptyReasoningText, translationOptions = null) {
  const header = widget.querySelector('.reasoning-header');
  const headerToggle = widget.querySelector('.reasoning-header-toggle');
  const contentEl = widget.querySelector('.reasoning-content');
  const stepsList = widget.querySelector('.reasoning-steps-list');
  const pendingApprovalList = widget.querySelector('.reasoning-pending-approval-list');
  const actionsEl = widget.querySelector('.reasoning-widget-actions');
  if (!header || !headerToggle || !contentEl || !stepsList || !pendingApprovalList || !actionsEl) return;

  // Check if all steps are complete (progress events finished, waiting for LLM)
  // Steps with status "complete" are progress events; steps without status are API reasoning steps
  const hasProgressEvents = steps.some(s => s.status !== undefined);
  const allProgressComplete = hasProgressEvents && steps.every(s => s.status === undefined || s.status === 'complete');
  const hasApiReasoningSteps = steps.some(s => s.status === undefined);
  const allStepsComplete = allProgressComplete && !hasApiReasoningSteps && !state.hasText;

  // "Action required" should only be shown/flashed when there is an actionable
  // approval request for the user to click right now.
  // During approval-resume streaming, waiting_approval steps may still be present
  // transiently, but no further user action is needed after a submit.
  const needsUserApprovalAction = steps.some(s => {
    if (!(s && s.is_approval_request)) return false;
    if (s.approval_status === 'approved' || s.approval_status === 'denied') return false;

    // If the user already submitted this request in this widget state,
    // suppress "action required" flashing for it.
    if (
      state.approvalSubmitted
      && state.submittedRequestId
      && s.approval_request_id
      && s.approval_request_id === state.submittedRequestId
    ) {
      return false;
    }
    return true;
  });

  // Store text options on header for toggle handler to use
  header.dataset.thinkingText = thinkingText;
  header.dataset.showReasoningText = showReasoningText;
  header.dataset.generatingText = generatingText;
  header.dataset.reasoningStepsText = reasoningStepsText;
  header.dataset.emptyReasoningText = emptyReasoningText || t('processing_steps_empty_fallback', 'Processing steps will appear here shortly.');
  header.dataset.hasApproval = needsUserApprovalAction;
  header.dataset.lastStepTitle = steps.length > 0 ? steps[steps.length - 1].title : '';
  header.dataset.lastStepToolType = steps.length > 0 ? (steps[steps.length - 1].tool_type || '') : '';
  state.translationOptions = translationOptions;

  // Update header text
  const titleEl = headerToggle.querySelector('.reasoning-title');
  if (titleEl) {
    titleEl.textContent = getHeaderText(state, header.dataset.lastStepTitle, header.dataset.lastStepToolType, thinkingText, showReasoningText, generatingText, reasoningStepsText, allStepsComplete, needsUserApprovalAction);
    if (needsUserApprovalAction) {
      header.classList.add('warning');
      if (!state.expanded) {
        // Auto-expand on approval required
        headerToggle.click();
      }
      // Flash title logic could go here or in a separate observer
      flashTitle(getActionRequiredTitle(widget));
    } else {
      header.classList.remove('warning');
      stopTitleFlash();
    }
  }

  // Update pulse animation
  if (isReasoning) {
    header.classList.add('reasoning-active');
  } else {
    header.classList.remove('reasoning-active');
  }

  renderReasoningHeaderActions(widget, state, translationOptions);

  const renderSignature = getReasoningContentSignature(steps, state, isReasoning, translationOptions);
  const contentChanged = widget.dataset.renderSignature !== renderSignature;
  const approvalStep = getLatestActionableApprovalStep(steps, state);
  const pendingApprovalSteps = approvalStep ? getPendingApprovalSteps(steps, state) : [];
  const pendingApprovalSet = new Set(pendingApprovalSteps);
  const stepEntries = steps.map((step, index) => ({step, index}));
  const nonPendingStepEntries = approvalStep
    ? stepEntries.filter(({step}) => !pendingApprovalSet.has(step))
    : stepEntries;
  const pendingApprovalEntries = approvalStep
    ? stepEntries.filter(({step}) => pendingApprovalSet.has(step))
    : [];

  if (widget.dataset.renderSignature !== renderSignature) {
    syncReasoningStepsList(
      stepsList,
      nonPendingStepEntries,
      state,
      widget.dataset.messageId,
      header.dataset.emptyReasoningText,
      steps.length === 0,
    );
    syncReasoningStepsList(
      pendingApprovalList,
      pendingApprovalEntries,
      state,
      widget.dataset.messageId,
      '',
      false,
    );
    widget.dataset.renderSignature = renderSignature;
  }

  pendingApprovalList.classList.toggle('has-pending-approvals', pendingApprovalEntries.length > 0);
  pendingApprovalList.classList.toggle('has-prior-steps', nonPendingStepEntries.length > 0);
  const actionsParts = [];
  if (approvalStep) {
    actionsParts.push(buildApprovalFooterHtml(approvalStep, pendingApprovalSteps, widget.dataset.messageId));
  }
  if (translationOptions?.translationError) {
    actionsParts.push(`<div class="text-danger small">${escapeHtml(translationOptions.translationError)}</div>`);
  }
  actionsEl.innerHTML = actionsParts.join('');
  setupApprovalReviewGate(widget, pendingApprovalList, actionsEl, approvalStep, state);

  if (contentChanged) {
    window.requestChatNextScrollToBottom?.();
  }

  maybeFocusLatestApprovalButton(widget, steps, state);
}

function renderReasoningHeaderActions(widget, state, translationOptions = null) {
  const headerActionsEl = widget.querySelector('.reasoning-header-actions');
  if (!headerActionsEl) return;

  if (
    !state.expanded
    || !translationOptions
    || !translationOptions.canTranslateProcessingSteps
    || !translationOptions.translateUrl
  ) {
    headerActionsEl.innerHTML = '';
    return;
  }

  const messageId = widget.dataset.messageId;
  headerActionsEl.innerHTML = `
    <button type="button"
            class="btn btn-sm btn-outline-secondary reasoning-translate-btn"
            data-translating-text="${escapeHtml(translationOptions.translatingText)}"
            data-original-text="${escapeHtml(translationOptions.translateText)}"
            title="${escapeHtml(translationOptions.translateTitle)}"
            hx-post="${escapeHtml(translationOptions.translateUrl)}"
            hx-target="#reasoning-section-${messageId}"
            hx-swap="outerHTML"
            hx-on:click="event.stopPropagation()"
            hx-on::before-request="this.disabled = true; this.querySelector('.reasoning-translate-btn-label').textContent = this.dataset.translatingText;"
            hx-on::response-error="this.disabled = false; this.querySelector('.reasoning-translate-btn-label').textContent = this.dataset.originalText;">
      <span class="spinner-border spinner-border-sm reasoning-translate-spinner htmx-indicator me-1"
            role="status"
            aria-hidden="true"></span>
      <i class="bi bi-translate me-1 reasoning-translate-icon"></i>
      <span class="reasoning-translate-btn-label">${escapeHtml(translationOptions.translateText)}</span>
    </button>
  `;

  if (typeof htmx !== 'undefined') {
    htmx.process(headerActionsEl);
  }
}

/**
 * Submit approval for a tool call.
 * Uses HTMX to fetch HTML that sets up proper SSE streaming infrastructure.
 */
function submitApproval(requestId, approved, btnElement) {
  if (!requestId) return;

  clearApprovalInteractionFocus(btnElement);

  // Disable buttons and show loading state
  const container = btnElement.closest('.reasoning-step-approval, .reasoning-approval-actions');
  if (container) {
    container.querySelectorAll('button').forEach(b => b.disabled = true);
    btnElement.innerHTML = `<span class="spinner-border spinner-border-sm" role="status"></span> ${approved ? t('approving', 'Approving...') : t('denying', 'Denying...')}`;
  }

  // Get message ID - try multiple methods
  let messageId = null;

  // Method 1: Look for reasoning-widget with data-message-id attribute
  const reasoningWidget = btnElement.closest('.reasoning-widget');
  if (reasoningWidget && reasoningWidget.dataset.messageId) {
    messageId = reasoningWidget.dataset.messageId;
  }

  // Method 2: Look for reasoning-container-{id} parent
  if (!messageId && reasoningWidget) {
    const reasoningContainer = reasoningWidget.closest('.reasoning-widget-container');
    if (reasoningContainer && reasoningContainer.id) {
      messageId = reasoningContainer.id.replace('reasoning-container-', '');
    }
  }

  // Method 3: Look for response-{id} container with data-message-id attribute
  if (!messageId) {
    const responseContainer = btnElement.closest('[data-message-id]');
    if (responseContainer) {
      messageId = responseContainer.dataset.messageId;
    }
  }

  // Method 4: Look for reasoning-data-{id} sibling
  if (!messageId && reasoningWidget) {
    const parent = reasoningWidget.parentElement;
    if (parent) {
      const dataDiv = parent.querySelector('[id^="reasoning-data-"]');
      if (dataDiv) {
        messageId = dataDiv.id.replace('reasoning-data-', '');
      }
    }
  }

  // Method 5: Search upward for any element with id containing the message pattern
  if (!messageId) {
    let el = btnElement;
    while (el && !messageId) {
      if (el.id && el.id.match(/^(reasoning-container-|response-|message_)(\d+)$/)) {
        messageId = el.id.replace(/^(reasoning-container-|response-|message_)/, '');
        break;
      }
      el = el.parentElement;
    }
  }

  if (!messageId) {
    console.error("Could not determine message ID for approval");
    return;
  }

  // Stop flashing title
  if (typeof stopTitleFlash === 'function') {
    stopTitleFlash();
  }

  // Find the message container to swap content into
  const messageEl = document.getElementById(`message_${messageId}`);
  if (!messageEl) {
    console.error("Could not find message element for message", messageId);
    return;
  }

  // Find the .message-text div inside the message
  let messageTextEl = messageEl.querySelector('.message-text');
  if (!messageTextEl) {
    console.error("Could not find message-text element");
    return;
  }

  // Use HTMX to fetch the streaming setup HTML and swap it into the message
  const url = `/chat_next/message/${messageId}/approval/?approved=${approved}`;

  // Mark approval as submitted in state so we don't re-render buttons
  let state = reasoningState.get(messageId);
  if (state) {
    state.approvalSubmitted = approved ? 'approved' : 'denied';
    state.submittedRequestId = requestId;
  }

  htmx.ajax('GET', url, {
    target: messageTextEl,
    swap: 'innerHTML focus-scroll:false'
  }).then(() => {
    // Immediately render the reasoning widget synchronously (no RAF) so the
    // widget is rebuilt in the same task as the swap, before the browser paints.
    // This prevents the user from seeing a blank/collapsed widget frame.
    const container = document.getElementById(`reasoning-container-${messageId}`);
    const dataEl = document.getElementById(`reasoning-data-${messageId}`);
    if (container && dataEl) {
      renderReasoningFromDataNow(container, dataEl, messageId);
    }
  });
}

/**
 * Submit "Approve all" for a tool - approves this call and auto-approves future calls.
 * Uses HTMX to fetch HTML that sets up proper SSE streaming infrastructure.
 */
function submitApprovalAll(requestId, toolLabel, btnElement) {
  if (!requestId) return;

  clearApprovalInteractionFocus(btnElement);

  // Disable buttons and show loading state
  const container = btnElement.closest('.reasoning-step-approval, .reasoning-approval-actions');
  if (container) {
    container.querySelectorAll('button').forEach(b => b.disabled = true);
    btnElement.innerHTML = `<span class="spinner-border spinner-border-sm" role="status"></span> ${t('approving', 'Approving...')}`;
  }

  // Get message ID using the same methods as submitApproval
  let messageId = null;
  const reasoningWidget = btnElement.closest('.reasoning-widget');
  if (reasoningWidget && reasoningWidget.dataset.messageId) {
    messageId = reasoningWidget.dataset.messageId;
  }
  if (!messageId && reasoningWidget) {
    const reasoningContainer = reasoningWidget.closest('.reasoning-widget-container');
    if (reasoningContainer && reasoningContainer.id) {
      messageId = reasoningContainer.id.replace('reasoning-container-', '');
    }
  }
  if (!messageId) {
    const responseContainer = btnElement.closest('[data-message-id]');
    if (responseContainer) {
      messageId = responseContainer.dataset.messageId;
    }
  }
  if (!messageId && reasoningWidget) {
    const parent = reasoningWidget.parentElement;
    if (parent) {
      const dataDiv = parent.querySelector('[id^="reasoning-data-"]');
      if (dataDiv) {
        messageId = dataDiv.id.replace('reasoning-data-', '');
      }
    }
  }
  if (!messageId) {
    let el = btnElement;
    while (el && !messageId) {
      if (el.id && el.id.match(/^(reasoning-container-|response-|message_)(\d+)$/)) {
        messageId = el.id.replace(/^(reasoning-container-|response-|message_)/, '');
        break;
      }
      el = el.parentElement;
    }
  }

  if (!messageId) {
    console.error("Could not determine message ID for approval");
    return;
  }

  // Stop flashing title
  if (typeof stopTitleFlash === 'function') {
    stopTitleFlash();
  }

  // Find the message container to swap content into
  const messageEl = document.getElementById(`message_${messageId}`);
  if (!messageEl) {
    console.error("Could not find message element for message", messageId);
    return;
  }

  let messageTextEl = messageEl.querySelector('.message-text');
  if (!messageTextEl) {
    console.error("Could not find message-text element");
    return;
  }

  // Use the /approval/all/ endpoint which adds the local tool to the auto-approve list
  const url = `/chat_next/message/${messageId}/approval/all/?tool_label=${encodeURIComponent(toolLabel)}`;

  // Mark approval as submitted in state so we don't re-render buttons
  let state = reasoningState.get(messageId);
  if (state) {
    state.approvalSubmitted = 'approved';
    state.submittedRequestId = requestId;
  }

  htmx.ajax('GET', url, {
    target: messageTextEl,
    swap: 'innerHTML focus-scroll:false'
  }).then(() => {
    const container = document.getElementById(`reasoning-container-${messageId}`);
    const dataEl = document.getElementById(`reasoning-data-${messageId}`);
    if (container && dataEl) {
      renderReasoningFromDataNow(container, dataEl, messageId);
    }
  });
}

/**
 * Determine header text based on current state.
 */
function getHeaderText(state, lastStepTitle, lastStepToolType, thinkingText, showReasoningText, generatingText, reasoningStepsText, allStepsComplete, hasPendingApproval) {
  if (hasPendingApproval) {
    return t('approval_required', "⚠️ Approval Required");
  } else if (state.expanded) {
    return reasoningStepsText;
  } else if (!state.isFinished && lastStepToolType === 'compaction' && lastStepTitle) {
    // During an active stream, prefer explicit compaction progress over the
    // generic "Generating final response" / "Thinking" fallback so users can
    // see that context management is actively happening.
    return lastStepTitle;
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

let titleFlashInterval;
let originalTitle;

function flashTitle(message) {
  if (titleFlashInterval) return;
  originalTitle = document.title;
  let showingMessage = false;

  titleFlashInterval = setInterval(() => {
    document.title = showingMessage ? originalTitle : message;
    showingMessage = !showingMessage;
  }, 1000);
}

function stopTitleFlash() {
  if (titleFlashInterval) {
    clearInterval(titleFlashInterval);
    titleFlashInterval = null;
    if (originalTitle) {
      document.title = originalTitle;
    }
  }
}

/**
 * Open the tool output modal for a specific processing step.
 * Fetches the modal inner HTML from the server and renders the requested output.
 */
function openToolOutputModal(messageId, stepIndex) {
  const modalEl = document.getElementById('tool-output-modal');
  const inner = document.getElementById('tool-output-modal-inner');
  const spinner = document.getElementById('tool-output-modal-spinner');
  const titleEl = document.getElementById('tool-output-modal-title');
  if (!modalEl || !inner) return;

  // Clear previous content and show spinner
  inner.innerHTML = '';
  if (titleEl) titleEl.textContent = '';
  if (spinner) spinner.style.display = '';

  // Show modal immediately (content loads inside it)
  bootstrap.Modal.getOrCreateInstance(modalEl).show();

  // Fetch inner content
  fetch(`/chat_next/message/${messageId}/tool_output/${stepIndex}/`)
    .then(function (r) {return r.text();})
    .then(function (html) {
      if (spinner) spinner.style.display = 'none';
      inner.innerHTML = html;
      // Execute inline scripts (e.g. the one in tool_output_modal_inner.html)
      inner.querySelectorAll('script').forEach(function (oldScript) {
        const newScript = document.createElement('script');
        newScript.textContent = oldScript.textContent;
        oldScript.replaceWith(newScript);
      });
    })
    .catch(function () {
      if (spinner) spinner.style.display = 'none';
    });
}

// Some approval updates arrive as reasoning-data OOB swaps before/after message swaps.
// Re-render the widget here for robustness across approval flows and for
// processing-step updates that land without a companion response swap.
document.addEventListener("htmx:oobAfterSwap", function (event) {
  const targetId = event.detail?.target?.id;
  if (!(targetId && targetId.startsWith('reasoning-data-'))) return;
  const messageId = targetId.replace('reasoning-data-', '');
  const responseEl = document.getElementById(`response-${messageId}`);
  if (responseEl) {
    render_reasoning(responseEl);
  }
});

window.render_reasoning = render_reasoning;
window.submitApproval = submitApproval;
window.submitApprovalAll = submitApprovalAll;
window.openToolOutputModal = openToolOutputModal;

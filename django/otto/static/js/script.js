document.addEventListener('htmx:afterRequest', function (event) {
  if (event.detail.target.id === 'feedback-form') {
    var modal = bootstrap.Modal.getInstance(document.getElementById('modal'));
    modal.hide();
  }
});

// Global HTMX error handler: detect connection issues and notify user
(function () {
  let consecutiveErrors = 0;
  let errorToastShown = false;

  function showConnectionErrorToast() {
    if (errorToastShown) return;
    errorToastShown = true;
    const toastsContainer = document.getElementById('toasts');
    if (!toastsContainer) return;
    const toast = document.createElement('div');
    toast.className = 'toast px-2 py-1 warning keep-open';
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    toast.setAttribute('tabindex', '-1');
    toast.id = 'htmx-connection-error-toast';
    const i18n = window.OTTO_I18N || {};
    const connectionMessage = i18n.connectionLostMessage || 'Connection lost. Please refresh the page.';
    const refreshText = i18n.refreshButton || 'Refresh';
    const closeText = i18n.closeButton || 'Close';
    toast.innerHTML = `
      <div class="row g-0 align-items-center">
        <div class="col-1" style="padding: .75rem .5rem;">
          <i class="bi bi-exclamation-triangle warning-toast-icon"></i>
        </div>
        <div class="col-9 flex-grow-1 toast-body">
          <span class="fw-semibold">${connectionMessage}</span>
          <button class="btn btn-sm btn-warning ms-2" onclick="location.reload()">
            <i class="bi bi-arrow-clockwise"></i> ${refreshText}
          </button>
        </div>
        <div class="col-1" style="padding: .75rem 0;">
          <button type="button" class="btn-close" style="font-size: .75rem"
                  data-bs-dismiss="toast" aria-label="${closeText}"></button>
        </div>
      </div>
    `;
    toastsContainer.prepend(toast);
    const bsToast = new bootstrap.Toast(toast, {delay: 60000});
    bsToast.show();
  }

  function dismissConnectionErrorToast() {
    const toast = document.getElementById('htmx-connection-error-toast');
    if (toast) {
      const bsToast = bootstrap.Toast.getInstance(toast);
      if (bsToast) bsToast.hide();
      toast.remove();
    }
    errorToastShown = false;
  }

  document.addEventListener('htmx:afterRequest', function (event) {
    if (event.detail.successful) {
      consecutiveErrors = 0;
      dismissConnectionErrorToast();
    } else if (event.detail.failed && event.detail.xhr) {
      consecutiveErrors++;
      if (consecutiveErrors >= 3) {
        showConnectionErrorToast();
      }
    }
  });

  document.addEventListener('htmx:sendError', function () {
    consecutiveErrors++;
    if (consecutiveErrors >= 2) {
      showConnectionErrorToast();
    }
  });

  document.addEventListener('htmx:timeout', function () {
    consecutiveErrors++;
    if (consecutiveErrors >= 2) {
      showConnectionErrorToast();
    }
  });
})();

(function () {
  htmx.onLoad(() => {
    let remove_these_toasts = [];
    let uniqueFound = false;
    htmx.findAll(".toast").forEach((element) => {
      const toastOptions = {delay: 5000};
      let toast = bootstrap.Toast.getInstance(element);

      // Handle unique toasts
      if (element.classList.contains("unique")) {
        if (uniqueFound) {
          remove_these_toasts.push(element);
        } else {
          uniqueFound = true;
        }
      }
      // Remove hidden toasts (optional)
      if (toast && !toast.isShown()) {
        toast.dispose();
        element.remove();
      }

      // Show new ones
      if (!toast) {
        if (element.classList.contains("keep-open")) {
          toastOptions.delay = 60000;
        }
        const toast = new bootstrap.Toast(element, toastOptions);
        toast.show();
        if (element.classList.contains("focus")) {
          element.focus();
        }
      }

      // If the element has a link, remove the toast when link is clicked
      let element_link = element.querySelector("a");
      if (element_link) {
        element_link.addEventListener("click", (e) => {
          let element = e.target.closest(".toast");
          if (element) {
            // Without the timeout, the response message rarely appears
            setTimeout(() => {
              element.remove();
            }, 1);
          }
        });
      }
    });
    remove_these_toasts.forEach((element) => {
      element.remove();
    });
    const costGroupSwitcher = document.querySelector("#cost-group-switcher-container");
    if (costGroupSwitcher) {
      costGroupSwitcher.classList.remove("d-none");
    }
  });
})();

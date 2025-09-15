// Global navigation guard for active uploads (chat, librarian, etc.)
// Provides window.setUploadsInProgress(state) and tracks window.uploadsInProgress
// Exclusions:
//  - Clicks on cancel buttons (.dff-cancel)
//  - Clicks that open Bootstrap modals ([data-bs-toggle="modal"], [data-toggle="modal"])
// Navigation elements monitored:
//  a[href], button[hx-get], button[hx-post], .nav-link, .list-group-item
(function () {
  if (window.__navigationGuardInitialized) {return;}
  window.__navigationGuardInitialized = true;

  window.uploadsInProgress = window.uploadsInProgress || false;

  function navigationClickHandler(e) {
    // Ignore cancel button on file upload progress items
    if (e.target.closest('.dff-cancel')) {
      return;
    }
    // Ignore modal open triggers
    if (e.target.closest('[data-bs-toggle="modal"], [data-toggle="modal"]')) {
      return;
    }
    const target = e.target.closest('a[href], button[hx-get], button[hx-post], .nav-link, .list-group-item');
    if (target && window.uploadsInProgress) {
      e.preventDefault();
      e.stopPropagation();
      const confirmLeave = confirm(window.uploadsInProgressMessage || "Uploads are still in progress. Are you sure you want to leave?");
      if (confirmLeave) {
        window.setUploadsInProgress(false);
        setTimeout(() => target.click(), 0); // async to release handler
      }
    }
  }

  window.setUploadsInProgress = function (state) {
    const prev = window.uploadsInProgress;
    window.uploadsInProgress = !!state;
    if (window.uploadsInProgress && !prev) {
      document.addEventListener('click', navigationClickHandler, true);
    } else if (!window.uploadsInProgress && prev) {
      document.removeEventListener('click', navigationClickHandler, true);
    }
  };
})();

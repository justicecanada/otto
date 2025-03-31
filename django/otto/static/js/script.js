document.addEventListener('htmx:afterRequest', function (event) {
  if (event.detail.target.id === 'feedback-form') {
    var modal = bootstrap.Modal.getInstance(document.getElementById('modal'));
    modal.hide();
  }
});

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
  });
})();

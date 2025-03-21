document.addEventListener('htmx:afterRequest', function (event) {
  if (event.detail.target.id === 'feedback-form') {
    var modal = bootstrap.Modal.getInstance(document.getElementById('modal'));
    modal.hide();
  }
});

(function () {
  const toastOptions = {delay: 5000};

  htmx.onLoad(() => {
    htmx.findAll(".toast").forEach((element) => {
      let toast = bootstrap.Toast.getInstance(element);

      // Remove hidden toasts (optional)
      if (toast && !toast.isShown()) {
        toast.dispose();
        element.remove();
      }

      // Show new ones
      if (!toast) {
        const toast = new bootstrap.Toast(element, toastOptions);
        toast.show();
      }
    });
  });
})();


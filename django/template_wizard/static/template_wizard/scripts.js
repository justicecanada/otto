// Template wizard scripts
function setActiveTab(e) {
  const tabs = document.querySelectorAll('#top-tabs .nav-link');
  tabs.forEach(tab => {
    tab.classList.remove('active');
    tab.classList.remove('fw-semibold');
  });
  e.classList.add('active');
  e.classList.add('fw-semibold');
}

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

// Get field names from Django context (these will be set in the HTML)
// allFields and topLevelFields must be defined in the HTML before this script is loaded.

function insertFields(fields) {
  const textarea = document.getElementById('id_template_html');
  if (!textarea) return;
  // Format each field as a markdown heading followed by {{ field_name }}
  const open = '{' + '{';
  const close = '}' + '}';
  const insertText = fields.map(f => `## ${f.name}\n\n` + open + ' ' + f.slug + ' ' + close + `\n\n`).join('');
  // Insert at cursor position if focused, else append on a new line
  if (document.activeElement === textarea && (textarea.selectionStart || textarea.selectionStart === 0)) {
    const startPos = textarea.selectionStart;
    const endPos = textarea.selectionEnd;
    textarea.value = textarea.value.substring(0, startPos)
      + insertText
      + textarea.value.substring(endPos, textarea.value.length);
    textarea.selectionStart = textarea.selectionEnd = startPos + insertText.length;
    textarea.focus();
  } else {
    // Ensure a newline before appending if not already present
    textarea.value = textarea.value.replace(/\n?$/, '\n') + insertText;
    textarea.focus();
  }
}

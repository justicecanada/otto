(function () {
  // FILTERS: Show/hide acts and regulation checkboxes
  const textBox = document.getElementById('text_filter');
  const enSwitch = document.getElementById('english_filter');
  const frSwitch = document.getElementById('french_filter');
  const showAllBtn = document.getElementById('show_all_btn');
  const showSelectedBtn = document.getElementById('show_selected_btn');

  // SELECTION MODIFIERS: Check or uncheck acts and regulation checkboxes
  const clearBtn = document.getElementById('clear_btn');
  const actsBtn = document.getElementById('all_acts_btn');
  const regsBtn = document.getElementById('all_regs_btn');
  const enablingBtn = document.getElementById('enabling_btn');
  const enabledByBtn = document.getElementById('enabled_by_btn');
  const sameEnablingBtn = document.getElementById('same_enabling_btn');
  const bothLangBtn = document.getElementById('both_lang_btn');

  // Checkboxes
  const acts = document.querySelectorAll('#acts_fieldset input');
  const regs = document.querySelectorAll('#regs_fieldset input');

  function updateSelectionCount() {
    let selectedActs = document.querySelectorAll('#acts_fieldset input:checked').length;
    let selectedRegs = document.querySelectorAll('#regs_fieldset input:checked').length;
    document.getElementById('act_selected_count').textContent = selectedActs;
    document.getElementById('reg_selected_count').textContent = selectedRegs;
  }

  function updateHiddenCount() {
    // Count all the #acts_fieldset div.form-check that have display:none
    let hiddenActsCount = document.querySelectorAll('#acts_fieldset div.form-check[style="display: none;"]').length;
    let hiddenRegsCount = document.querySelectorAll('#regs_fieldset div.form-check[style="display: none;"]').length;
    let actHiddenCounter = document.getElementById('act_hidden_count');
    const noActsFound = document.getElementById('no-acts-found');
    if (hiddenActsCount > 0) {
      actHiddenCounter.style.display = 'inline';
      actHiddenCounter.querySelector("span").textContent = hiddenActsCount;
      noActsFound.classList.remove('d-none');
    } else {
      actHiddenCounter.style.display = 'none';
      noActsFound.classList.add('d-none');
    }
    let regHiddenCounter = document.getElementById('reg_hidden_count');
    const noRegsFound = document.getElementById('no-regulations-found');
    if (hiddenRegsCount > 0) {
      regHiddenCounter.style.display = 'inline';
      regHiddenCounter.querySelector("span").textContent = hiddenRegsCount;
      noRegsFound.classList.remove('d-none');
    } else {
      regHiddenCounter.style.display = 'none';
      noRegsFound.classList.add('d-none');
    }
  }

  // SELECTION MODIFIERS BEHAVIOUR
  acts.forEach((act) => act.addEventListener('change', updateSelectionCount));
  regs.forEach((reg) => reg.addEventListener('change', updateSelectionCount));
  clearBtn.addEventListener('click', function () {
    acts.forEach((act) => act.checked = false);
    regs.forEach((reg) => reg.checked = false);
    updateSelectionCount();
  });

  actsBtn.addEventListener('click', function () {
    // Select all visible acts
    document.querySelectorAll('#acts_fieldset input').forEach((act) => {
      if (act.parentElement.style.display !== 'none') {
        act.checked = true;
      }
    });
    updateSelectionCount();
  });

  regsBtn.addEventListener('click', function () {
    // Select all visible regulations
    document.querySelectorAll('#regs_fieldset input').forEach((reg) => {
      if (reg.parentElement.style.display !== 'none') {
        reg.checked = true;
      }
    });
    updateSelectionCount();
  });

  bothLangBtn.addEventListener('click', function () {
    document.querySelectorAll('#acts_fieldset input:checked').forEach((act) => {
      let law_id = act.getAttribute("data-law_id");
      // Check all the acts that have this data attribute
      document.querySelectorAll(`#acts_fieldset input[data-law_id="${law_id}"]`).forEach((act) => act.checked = true);
    });
    document.querySelectorAll('#regs_fieldset input:checked').forEach((reg) => {
      let law_id = reg.getAttribute("data-law_id");
      // Check all the regs that have this data attribute
      document.querySelectorAll(`#regs_fieldset input[data-law_id="${law_id}"]`).forEach((reg) => reg.checked = true);
    });
    updateSelectionCount();
  });

  // FILTERS BEHAVIOUR
  textBox.addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
    }
  });
  textBox.addEventListener('input', function () {
    // Hide all acts and regulations that don't include the substring
    let substring = textBox.value.toLowerCase();
    // De-accent the substring and replace curly quotes with normal ones
    substring = substring.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    substring = substring.replace(/[‘’]/g, "'");

    let matching_checkboxes = [];
    let acts_regs_labels = [
      ...document.querySelectorAll("#acts_fieldset label"),
      ...document.querySelectorAll("#regs_fieldset label")
    ];
    acts_regs_labels.forEach((label) => {
      let act_name = label.textContent.toLowerCase();
      act_name = act_name.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      act_name = act_name.replace(/[‘’]/g, "'");
      let act_lang = label.parentElement.querySelector("input").getAttribute("data-lang");
      if (
        act_name.includes(substring) &&
        ((act_lang === 'eng' && enSwitch.checked) || (act_lang === 'fra' && frSwitch.checked))
      ) {
        matching_checkboxes.push(label.parentElement);
      }
    });

    // Show all checkboxes that match the substring
    acts.forEach((act) => act.parentElement.style.display = 'none');
    regs.forEach((reg) => reg.parentElement.style.display = 'none');
    matching_checkboxes.forEach((checkbox) => checkbox.style.display = 'block');
    updateHiddenCount();
  });

  showAllBtn.addEventListener('click', function () {
    textBox.value = '';
    enSwitch.checked = true;
    frSwitch.checked = true;
    textBox.dispatchEvent(new Event('input'));
  });

  showSelectedBtn.addEventListener('click', function () {
    textBox.value = '';
    enSwitch.checked = true;
    frSwitch.checked = true;
    checkedActs = document.querySelectorAll('#acts_fieldset input:checked');
    checkedRegs = document.querySelectorAll('#regs_fieldset input:checked');
    acts.forEach((act) => act.parentElement.style.display = 'none');
    regs.forEach((reg) => reg.parentElement.style.display = 'none');
    checkedActs.forEach((act) => act.parentElement.style.display = 'block');
    checkedRegs.forEach((reg) => reg.parentElement.style.display = 'block');
    updateHiddenCount();
  });

  enSwitch.addEventListener('change', function () {
    // Fake a textbox input event
    textBox.dispatchEvent(new Event('input'));
  });
  frSwitch.addEventListener('change', function () {
    // Fake a textbox input event
    textBox.dispatchEvent(new Event('input'));
  });

  enablingBtn.addEventListener('click', function () {
    // Select laws whose "data-ref_number" matches the "data-enabling_authority"
    // of selected.
    // In reality, only Regulations have an enabling authority, and only
    // Acts are referred to as an enabling authority of a Regulation.
    // But to be thorough we will include all possibilities.
    let selected = [
      ...document.querySelectorAll('#acts_fieldset input:checked'),
      ...document.querySelectorAll('#regs_fieldset input:checked')
    ];
    let selected_enabling_authorities = new Set();
    selected.forEach((law) => {
      selected_enabling_authorities.add(law.getAttribute('data-enabling_authority'));
    });
    acts_and_regs = [...acts, ...regs];
    acts_and_regs.forEach((law) => {
      if (
        selected_enabling_authorities.has(law.getAttribute('data-ref_number'))
        && law.parentElement.style.display !== 'none'
      ) {
        law.checked = true;
      }
    });
    updateSelectionCount();
  });
  enabledByBtn.addEventListener('click', function () {
    // Select laws whose "data-enabling_authority" matches the "data-ref_number"
    // of selected.
    let selected = [
      ...document.querySelectorAll('#acts_fieldset input:checked'),
      ...document.querySelectorAll('#regs_fieldset input:checked')
    ];
    let selected_ref_numbers = new Set();
    selected.forEach((law) => {
      selected_ref_numbers.add(law.getAttribute('data-ref_number'));
    });
    acts_and_regs = [...acts, ...regs];
    acts_and_regs.forEach((law) => {
      if (
        selected_ref_numbers.has(law.getAttribute('data-enabling_authority'))
        && law.parentElement.style.display !== 'none'
      ) {
        law.checked = true;
      }
    });
    updateSelectionCount();
  });
  sameEnablingBtn.addEventListener('click', function () {
    // Select laws whose "data-enabling_authority" matches the "data-enabling_authority"
    // of selected.
    let selected = [
      ...document.querySelectorAll('#acts_fieldset input:checked'),
      ...document.querySelectorAll('#regs_fieldset input:checked')
    ];
    let selected_enabling_authorities = new Set();
    selected.forEach((law) => {
      selected_enabling_authorities.add(law.getAttribute('data-enabling_authority'));
    });
    acts_and_regs = [...acts, ...regs];
    acts_and_regs.forEach((law) => {
      if (
        selected_enabling_authorities.has(law.getAttribute('data-enabling_authority'))
        && law.parentElement.style.display !== 'none'
      ) {
        law.checked = true;
      }
    });
    updateSelectionCount();
  });
})();

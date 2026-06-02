let selectedUsers = [];

function copyToClipboard() {
  if (selectedUsers.length === 0) {
    alert('Select at least one user');
    return;
  }
  let emailAddresses = selectedUsers.map(user => user.upn).join('; ');
  if (emailAddresses.length > 0) {
    emailAddresses += ';';
  }
  navigator.clipboard.writeText(emailAddresses).then(function () {
    alert('Email addresses copied to clipboard');
  }, function (err) {
    alert('Could not copy text: ', err);
  });
}

function getUserIds() {
  return selectedUsers.map(user => user.id).join(',');
}

document.addEventListener('DOMContentLoaded', function () {
  const usersTable = document.getElementById('users');
  const downloadUsersLink = document.getElementById('download-users-link');
  const downloadUsersSpinner = document.getElementById('download-users-spinner');
  const downloadUsersStatus = document.getElementById('download-users-status');
  const bulkUploadForm = document.getElementById('bulk-upload-form');
  const bulkUploadSubmitButton = document.getElementById('bulk-upload-submit-button');
  const bulkUploadSubmitSpinner = document.getElementById('bulk-upload-submit-spinner');
  const uploadUsersStatus = document.getElementById('upload-users-status');
  const showAllColumnsToggle = document.getElementById('toggle-show-all-columns');
  const columnToggleInputs = Array.from(document.querySelectorAll('.manage-users-column-toggle'));

  const searchableColumnKeysByIndex = {
    1: 'upn',
    2: 'entra_status',
    3: 'job_title',
    4: 'preferred_language',
    5: 'last_login',
    6: 'cost_7_days',
    7: 'cost_30_days',
    8: 'cost_all_time',
    9: 'roles',
    10: 'cost_groups',
    11: 'teams',
  };

  function getVisibleSearchFields() {
    const fields = ['upn'];
    columnToggleInputs.forEach(function (input) {
      const index = Number(input.dataset.columnIndex);
      const fieldKey = searchableColumnKeysByIndex[index];
      if (input.checked && fieldKey) {
        fields.push(fieldKey);
      }
    });
    return fields;
  }

  function setDownloadUsersLoading(isLoading) {
    if (downloadUsersLink) {
      downloadUsersLink.classList.toggle('disabled', isLoading);
      downloadUsersLink.setAttribute('aria-disabled', isLoading ? 'true' : 'false');
    }
    if (downloadUsersSpinner) {
      downloadUsersSpinner.classList.toggle('d-none', !isLoading);
    }
    if (downloadUsersStatus) {
      downloadUsersStatus.textContent = isLoading ? 'Preparing CSV download.' : '';
    }
  }

  function setBulkUploadLoading(isLoading) {
    if (bulkUploadSubmitButton) {
      bulkUploadSubmitButton.disabled = isLoading;
      bulkUploadSubmitButton.setAttribute('aria-disabled', isLoading ? 'true' : 'false');
    }
    if (bulkUploadSubmitSpinner) {
      bulkUploadSubmitSpinner.classList.toggle('d-none', !isLoading);
    }
    if (uploadUsersStatus) {
      uploadUsersStatus.textContent = isLoading ? 'Uploading CSV.' : '';
    }
  }

  function updateShowAllColumnsToggle() {
    if (!showAllColumnsToggle || columnToggleInputs.length === 0) {
      return;
    }

    const checkedCount = columnToggleInputs.filter(input => input.checked).length;
    showAllColumnsToggle.checked = checkedCount === columnToggleInputs.length;
    showAllColumnsToggle.indeterminate = checkedCount > 0 && checkedCount < columnToggleInputs.length;
  }

  function parseDownloadFilename(contentDisposition) {
    if (!contentDisposition) {
      return 'otto_users.csv';
    }

    const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
      return decodeURIComponent(utf8Match[1]);
    }

    const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
    if (filenameMatch && filenameMatch[1]) {
      return filenameMatch[1];
    }

    return 'otto_users.csv';
  }

  if (downloadUsersLink) {
    let downloadUsersInFlight = false;

    downloadUsersLink.addEventListener('click', async function (event) {
      event.preventDefault();
      if (downloadUsersInFlight) {
        return;
      }

      downloadUsersInFlight = true;
      setDownloadUsersLoading(true);

      try {
        const response = await fetch(downloadUsersLink.href, {
          method: 'GET',
          credentials: 'same-origin',
        });

        if (!response.ok) {
          throw new Error(`Download failed with status ${response.status}`);
        }

        const blob = await response.blob();
        const downloadUrl = window.URL.createObjectURL(blob);
        const tempLink = document.createElement('a');
        tempLink.href = downloadUrl;
        tempLink.download = parseDownloadFilename(response.headers.get('Content-Disposition'));
        document.body.appendChild(tempLink);
        tempLink.click();
        tempLink.remove();
        window.URL.revokeObjectURL(downloadUrl);
      } catch (error) {
        console.error('Could not download users CSV', error);
        alert('Could not download CSV. Please try again.');
      } finally {
        downloadUsersInFlight = false;
        setDownloadUsersLoading(false);
      }
    });
  }

  if (bulkUploadForm) {
    bulkUploadForm.addEventListener('submit', function () {
      setBulkUploadLoading(true);
    });
  }

  if (!usersTable || typeof DataTable === 'undefined') {
    updateShowAllColumnsToggle();
    return;
  }

  const ajaxUrl = usersTable.dataset.ajaxUrl;
  const editDropdownMenu = document.getElementById('editDropdownMenu');

  const dataTableConfig = {
    columnDefs: [
      {
        targets: 0,
        orderable: false,
        render: DataTable.render.select(),
      },
      {
        targets: [6, 7, 8],
        render: function (data, type) {
          if (data && typeof data === 'object') {
            if (type === 'sort' || type === 'type') {
              return data.sort;
            }
            if (type === 'filter') {
              return data.filter || data.display;
            }
            return data.display;
          }
          return data;
        },
      },
      {
        targets: [2, 3, 4, 6, 8],
        visible: false,
      },
      {
        targets: [-1, -2], // Target the last and second to last column
        orderable: false, // Disable ordering
        searchable: false // Exclude it from search
      },
      {
        targets: -1, // Target the last column
        visible: false, // Hide the column        
      }
    ],
    select: {
      style: 'multi',
      selector: 'td:first-child',
    },
    layout: {
      topStart: 'pageLength',
      topEnd: 'search',
      bottomStart: 'info',
      bottomEnd: 'paging'
    },
    lengthMenu: [10, 25, 50, {label: 'All', value: -1}],
    order: [[1, 'asc']],
    paging: true,
    processing: true,
    serverSide: true,
    stateSave: false,


  };

  if (ajaxUrl) {
    dataTableConfig.ajax = {
      url: ajaxUrl,
      data: function (data) {
        data.visible_search_fields = getVisibleSearchFields().join(',');
      },
    };
  }

  const table = new DataTable(usersTable, dataTableConfig);

  function applyColumnVisibilityChanges() {
    columnToggleInputs.forEach(function (input) {
      const columnIndex = Number(input.dataset.columnIndex);
      table.column(columnIndex).visible(input.checked, false);
    });
    table.columns.adjust();
    table.ajax.reload(null, false);
    updateShowAllColumnsToggle();
  }

  columnToggleInputs.forEach(function (input) {
    input.addEventListener('change', function () {
      applyColumnVisibilityChanges();
    });
  });

  if (showAllColumnsToggle) {
    showAllColumnsToggle.addEventListener('change', function () {
      columnToggleInputs.forEach(function (input) {
        input.checked = showAllColumnsToggle.checked;
      });
      applyColumnVisibilityChanges();
    });
  }

  updateShowAllColumnsToggle();

  function getSelectedUsers() {
    const idColumnIndex = table.columns().count() - 1;
    return table
      .rows({selected: true})
      .data()
      .toArray()
      .map(row => ({id: row[idColumnIndex], upn: row[1]}));
  }

  table.on('select', function (e, dt, type, indexes) {
    if (type === 'row') {
      if (editDropdownMenu) {
        editDropdownMenu.classList.remove('d-none');
      }
      selectedUsers = getSelectedUsers();
    }
  });

  table.on('deselect', function (e, dt, type, indexes) {
    if (type === 'row') {
      if (table.rows({selected: true}).count() === 0 && editDropdownMenu) {
        editDropdownMenu.classList.add('d-none');
      }
      selectedUsers = getSelectedUsers();
    }
  });

  table.on('draw', function () {
    if (table.rows({selected: true}).count() === 0) {
      selectedUsers = [];
      if (editDropdownMenu) {
        editDropdownMenu.classList.add('d-none');
      }
    }

    if (window.htmx && typeof htmx.process === 'function') {
      htmx.process(table.table().body());
    }
  });
});

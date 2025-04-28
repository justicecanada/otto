let selectedUsers = [];
function copyToClipboard() {
  let emailAddresses = selectedUsers.map(user => user.upn).join('; ') + ';';
  navigator.clipboard.writeText(emailAddresses).then(function () {
    alert('Email addresses copied to clipboard');
  }, function (err) {
    alert('Could not copy text: ', err);
  });
}
function getUserIds() {
  let userIds = selectedUsers.map(user => user.id).join(',');
  return userIds;
}
$(document).ready(function () {
  let dataTableConfig = {
    columnDefs: [
      {
        orderable: false,
        render: DataTable.render.select(),
        targets: 0
      }
    ],
    select: {
      style: 'os',
      selector: 'td:first-child'
    },
    order: [[1, 'asc']]
  };
  var table = $('#users').DataTable(dataTableConfig);
  table.on('select', function (e, dt, type, indexes) {
    if (type === 'row') {
      var data = table
        .rows({selected: true})
        .data().toArray();

      document.getElementById('editDropdownMenu').classList.remove('d-none');
      selectedUsers = data.map(row => {
        let item = {
          id: row[7],
          upn: row[1]
        };
        return item;
      });
    }
  });

  table.on('deselect', function (e, dt, type, indexes) {
    if (type === 'row') {
      if (table.rows({selected: true}).count() == 0) {
        document.getElementById('editDropdownMenu').classList.add('d-none');
      }
      var data = table
        .rows({selected: true})
        .data().toArray();
      selectedUsers = data.map(row => {
        let item = {
          id: row[7],
          upn: row[1]
        };
        return item;
      });
    }
  });
});

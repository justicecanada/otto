# Librarian App Model Overview

## Overview

Libraries can be either:
* public (viewable to all JUS users)
* private (viewable to only the creator)
* shared (viewable and/or editable by multiple users)

There are 3 user roles that enable these options:
* Library admin: can manage user roles and edit library
* Library contributor: can edit library
* Library viewer: can view library

There is also an "is_public" boolean on the Library model to enable JUS-wide sharing.

Only users in the "Data stewards" group can create or administrate public libraries.

Users in group "Otto admins" have admin permissions on all public libraries.

## Models

- **Library:** Container housing various data sources. Analogous to a drive. The Q&A system can only search one Library at a time.
- **Data Source:** Represents managed data sources within libraries, such as a list of URLs or several related uploaded files. Analogous to a folder. Multiple data sources can be searched at a time, and can be selected/de-selected to narrow search results.
- **Document:** Maintains a relationship with the vector store. Each document is associated with a data source, and has metadata such as a user-defined title. A document can be a URL or an uploaded file.

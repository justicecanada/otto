# What is Otto

Otto is a suite of AI services developed by data specialists at Justice Canada with the goal of helping legal professionals improve the efficiency and accuracy of legal research, analysis, and decision-making.

## Table of Contents

- [What is Otto](#what-is-otto)
  - [Table of Contents](#table-of-contents)
  - [Development setup](#development-setup)
    - [Apply database migrations and fixtures, then run Django server](#loading-app-data)
    - [Generate Translations](#generate-translations)
    - [Load Legislation](#load-legislation)
  - [Contributing](#contributing)
    - [Translations](#translations)
      - [1. Model Level Translations](#1-model-level-translations)
      - [2. Python Code Level](#2-python-code-level)
      - [3. Template Level](#3-template-level)
    - [Running tests and seeing the coverage](#running-tests-and-seeing-the-coverage)
    - [Writing tests](#writing-tests)
    - [Logging](#logging)
  - [Deploy to Azure](#deploy-to-azure)
  - [Resetting the database](#if-all-else-fails)
  - [License and contact](#license-and-contact)


## Development setup

**Requirements:**
* Visual Studio Code (VScode) with Dev Containers extension
* Docker (with Hyper-V and WSL2 enabled)
* Git
* VPN (some Azure resources will not work if not connected)

> [!NOTE]  
> Note that the installation may take 5-10 minutes (longer if you are connected to VPN).

1. Clone this repository.
2. Start Docker.
3. In VScode, "Dev Containers: Open Folder in Container..." and select the directory you cloned this repo to.
4. In the Git sidebar, click "Manage unsafe folders" and mark the repository as safe.
5. From the terminal, run `bash dev_setup.sh` and follow the instructions.
6. You can now run the server from the "Run and debug" sidebar in VScode or just run `python django/manage.py runserver` in the VScode terminal.
7. Login to Otto using your Justice account.
8. From the terminal, run `python manage.py set_admin_user <firstname.lastname@justice.gc.ca>`.
9. You will now have full permissions when you refresh Otto. You can add other users using the "Manage users > Upload CSV" option. (CSV of pilot users is found in our shared drive).

After the initial setup, you will rarely have to build the dev containers again. You can just:

1. Start Docker Desktop
2. Open VScode, and if the container is not already opened, run "Dev Containers: Open Folder in Container..."
3. Start the Django server.

### Loading app data

All the commands you need to load data into Otto are in `django/initial_setup.sh`.

If you do not want to reset all your data (e.g. to preserve previously loaded Laws or Libraries), you can run individual commands.

For `reset_app_data`, you can specify the objects to reset. For example, to reset only the apps, terms, and groups data:

```bash
python manage.py reset_app_data apps terms groups
```

To reset the libraries and clear out the vector store, run the following command. This will delete all the data in the vector store!

```bash
python manage.py reset_app_data libraries
```

To populate the Corporate Q&A library, run the following command. However, please be aware that this will ingest data and generate costs.

```bash
python manage.py load_corporate_library
```

### Generate Translations

The following command[^1] will automatically translate (or use existing translations) for text flagged as requiring translation ([See the Translations section](#translations)).

You should generate translations before making a PR if your branch has modified or added any text strings.

  ```bash
  python manage.py load_app_localization
  ```

[^1]: To run the command you will need to have the [gettext binaries](https://mlocati.github.io/articles/gettext-iconv-windows.html) installed.

### Load Legislation

For the legislation search app to function, we must load the XML files into the database.

To download the [laws-lois-xml](https://github.com/justicecanada/laws-lois-xml) repo, and load an absolute minimum of laws (1 act, 1 regulation) into Django and the vector database, run the following:

```bash
python django/manage.py load_laws_xml --reset --small
```

To load around 50 laws into Django and the vector database, run the following. It should take around half an hour and cost $2:

```bash
python django/manage.py load_laws_xml --reset
```

* To load all laws (slow and quite expensive - around $20; 8 hours), add the `--full` flag.
* If you leave off `--reset` it should only add laws which aren't already loaded, so you can incrementally add more.

#### Speed up vector store queries

To speed up queries in the vector store, you may wish to build an HNSW index on the table.

(This is done automatically when the `--full` flag is used to load the laws.)

```bash
psql -U postgres -h postgres-service
```

Enter the password. Switch to the llama_index database and create the HNSW index. **This can take a while.** (an hour or more for the full set of laws).

```sql
\c llama_index
CREATE INDEX ON data_laws_lois__ USING hnsw (embedding vector_ip_ops) WITH (m = 25, ef_construction = 300);
```

### Celery Scheduler

To enable the celery scheduler in the development setup uncomment the `celery-beat` service found in `.devcontainer/docker-compose.yml`.

In `django/otto/celery.py` adjust the celery beat settings as needed. The following example shows a job running every morning at 5am UTC

``` python
app.conf.beat_schedule = {
  # Sync entra users every day at 5 am UTC
  "sync-entra-users-every-morning": {
      "task": "otto.tasks.sync_users",
      "schedule": crontab(hour=5, minute=0),
  }
}
```


## Contributing

* Don't push commits directly to `main`.
* Always branch off `main` to create a feature branch, e.g. `git checkout -b chatbot-error-messages`
* Before opening a pull request (PR), make sure your branch is up to date with the source branch by running `git merge origin/main`; resolve any conflicts.
* Run integration tests before opening a PR (see the instructions below). If the tests don't pass on your machine, they won't pass in the PR checks either.
* Write more tests if you have added new functionality (or are addressing a bug that wasn't previously caught by the tests).
* Give your PR a descriptive title using [conventional commits](https://kapeli.com/cheat_sheets/Conventional_Commits.docset/Contents/Resources/Documents/index), e.g.:
  * `fix: chatbot not displaying errors` for a bug fix
  * `feat: upload document preview` for a new feature
  * `chore: upgrade llama-index version`
  * `refactor: extract logic from models into utils`
* To indicate that the PR *isn't* ready to merge, create a `Draft PR`
* Get someone else to review your PR before merging it.

After your PR is merged:
  * On your workstation, `git checkout main` and `git pull`
  * Delete the branch that was just merged, e.g. `git branch -D chatbot-error-messages`
  * Create a new feature branch, if you are ready to do so.

### Translations

Translations happen in 3 different levels:
1. Model Level
2. Python Code Level
3. Template Level

#### 1. Model Level Translations

For any model that require translation:

1. Create a *translation.py* in your app directory.
2. Create a translation option class for every model to translate.
3. Register the model and the translation option class *(See otto/translation.py for example)*.   
4. Make sure to create new migrations and apply the changes
   
    ```bash
    python manage.py makemigrations #create new migrations
    python manage.py migrate # apply changes
    ```

5. Provide default and translated values in fixtures if necessary *(See otto/fixtures/\*.yaml)*


*For more information see official documentation of [django-modeltranslation](https://django-modeltranslation.readthedocs.io/en/latest/registration.html)*

#### 2. Python Code Level

Import the **gettext** module as its shorter alias of '_'

```python
from django.utils.translation import gettext as _
```

Translate strings using the **_()** function

```python
from django.http import HttpResponse
from django.utils.translation import gettext as _


def my_view(request):
    output = _("Welcome to my site.")
    return HttpResponse(output)
```

*For more information see official documentation of [django](https://docs.djangoproject.com/en/5.0/topics/i18n/translation/#internationalization-in-python-code)*

#### 3. Template Level

1. Make sure your code is ready for translation by having `{% load i18n %}` toward the top of your template code and surrounding any text with the `trans` tag or the `blocktrans` tag

    ```html
    {% load i18n %}

    <title>{% trans "This is the title." %}</title>

    {% blocktrans with numeric_value=thing.property %}This string will have {{ numeric_value }} inside.{% endblocktrans %}
    ```

*For more information, including how to translate your javascript code, see official documentation of [django](https://docs.djangoproject.com/en/5.0/topics/i18n/translation/#internationalization-in-template-code)*



### Running tests and seeing the coverage

Run collectstatic before running tests. You don't have to do this often, only when static files have changed. From the repo root:
```bash
python django/manage.py collectstatic --noinput
```

In PowerShell, from the repo root, paste this one-liner to run tests and display the results:
```bash
python -m coverage run --source=django -m pytest django; python -m coverage html; python -m coverage report
```

You can view the results in more detail by opening `htmlcov/index.html` in your browser.

### Writing tests

Writing tests of the views ensures that pages will at least load (no server error).

A test of a view can be found in `/django/otto/tests/test_views.py`:

```python
@pytest.mark.django_db
def test_homepage():
    client = Client()
    response = client.get(reverse("index"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    text = soup.get_text()
    assert "Otto" in text
```

You should also test your functions separately (unit tests).

Unit tests must be located in `/django/tests/`. Here is a simple test:

```python
def test_token_counter():
    n = num_tokens_from_string("A")
    assert n == 1
```


### Logging

Please log any actions user take while interacting with Otto using the *structlog* library and its *info* function.

```python
from structlog import get_logger

...

logger = get_logger(__name__)

...

logger.info("Insert logging message here")

```

You can also log any relevant information by adding a key/pair value to the log dictionary

```python
logger.info("Insert logging message here", x=1, y=2)
```

You should log errors using the *error()* function, critical messages using *critical()* and debugging messages using *debug()* [^2]

```python
logger.error("")
logger.critical("")
logger.debug("")
```

[^2]: To see debug messages, make sure to set **LOG_LEVEL** to *DEBUG* and/or **CELERY_DEBUG_LEVEL** to *DEBUG*

## Deploy to Azure

### Prerequisites

Before you begin, ensure you have met the following requirements:

- **PowerShell:** Installed and accessible on your system.
- **Git:** Installed and accessible from the command line.
- **Docker:** Installed and running on your machine.
- **Azure CLI (az):** Installed and accessible from the command line.
- **Azure Container Registry (ACR) Access:** Appropriate permissions to push images.
- **Dockerfile:** Located in the `./django` directory.
- **Git Repository Context:** Executed from within a Git repository or initialized directory.
- **Execution Permissions:** PowerShell script execution policy set to allow running scripts (`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process`).
- **VPN Connection:** Required to connect to Azure and other resources.

### Build and push the Docker image

Run the following script and follow the prompts:

The script will prompt you to enter three pieces of information:
- **Registry Name:** Enter the name of your Azure Container Registry (ACR) instance.
- **Version Number:** Enter the version number for the Docker image (e.g., v1.0.0).

The script will:
- Generate a unique build number based on the current date and time.
- Create a version.yaml file with the provided information.
- Copy the version.yaml file into the Docker build context.
- Log in to your Azure Container Registry.
- Build the Docker image with a specific tag based on the version and build number.
- Tag the Docker image as latest.
- Push both the versioned and latest tags to Azure Container Registry.
- Clean up the temporary version.yaml file.

```powershell
.\build_and_push_image.ps1
```

2. Setup and deploy to Azure Kubernetes Service

See `/setup` folder to follow the `README.md`. These steps will ensure the infrastructure is setup and that the AKS cluster is configured correctly. The final step is to deploy the run the `initial_setup.sh` on the coordinator node.

## If all else fails

If you are having migration issues and/or have run out of options for debugging your branch, try resetting your database.

> [!WARNING] 
> This will delete all of your data - be warned!

`python manage.py reset_database`

If things are even more messed up:
* Delete all your Docker containers, images and volumes
* Ensure you are sync'd with origin.
* Open the folder in VScode to rebuild the container from scratch.

## License and contact

For details, please see LICENSE.txt.

We cannot use a more permissive license than GPL until we remove these dependencies:
* html2text

### GPL 3 license

Copyright (c) 2024  His Majesty the King in Right of Canada

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

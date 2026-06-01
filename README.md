# Otto

> [!NOTE]
> The public copy of this repository does not include infrastructure code.

Otto is a platform designed to host a wide range of AI tools, data visualizations, and interactive solutions that address various efficiency needs across Justice Canada. Developed by data specialists, Otto streamlines processes, makes delivering valuable solutions easier, and enhances overall productivity for legal professionals.

Born out of the need to tackle data challenges that didn't fit neatly into existing corporate systems, Otto serves as a flexible hosting environment for:

- AI-powered tools for legal research and analysis
- Interactive data visualizations and dashboards
- Specialized data processing and management applications
- Custom-built solutions for specific departmental needs

**Key Features and Benefits**

Otto is designed to:

- **Empower Data Specialists**: Host and deploy diverse AI tools, specialized visuals, reports, and data applications.
- **Enhance Accessibility**: Provide non-technical users with simple, centralized interfaces to interact with complex AI and data tools.
- **Promote Agility**: Reduce hurdles in delivering solutions to users, enabling rapid implementation of new tools and visualizations.
- **Foster Innovation**: Encourage the development and implementation of cutting-edge AI and data solutions.
- **Build Community**: Serve as an open-source platform that encourages collaboration from developers across Justice Canada.
- **Adapt Flexibly**: Meet the changing needs of the Justice department with scalable capacity to handle varying demand.
- **Ensure Security**: Protect data and ensure compliance with Justice department standards.

As a platform for AI and data services, Otto helps legal professionals improve the efficiency and accuracy of legal research, analysis, and decision-making. It's an open-source, flexible tool for creating, hosting, and deploying a wide array of data and AI applications within the Justice Canada ecosystem.

## Table of Contents

- [About Otto](#otto)
- [Table of Contents](#table-of-contents)
- [Development setup](#development-setup)
  - [Apply database migrations and fixtures, then run Django server](#loading-app-data)
  - [Generate Translations](#generate-translations)
  - [Load Legislation](#load-legislation)
- [Contributing](#contributing)
  - [Pre-commit and Pre-push Hooks](#pre-commit-and-pre-push-hooks)
    - [Installing Pre-commit Hooks](#installing-pre-commit-hooks)
    - [What Runs on Pre-commit](#what-runs-on-pre-commit)
    - [What Runs on Pre-push](#what-runs-on-pre-push)
    - [Skipping Hooks (Not Recommended)](#skipping-hooks-not-recommended)
    - [Manual Hook Execution](#manual-hook-execution)
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

### With Docker or Rancher Desktop

**Requirements:**
* Visual Studio Code (VScode) with Dev Containers extension
* WSL
* Docker or Rancher Desktop (with Hyper-V and WSL2 enabled)
* Git for Windows

> [!NOTE]  
> Note that the installation may take 5-10 minutes (longer if you are connected to VPN).

1. Using Git for Windows, clone this repository somewhere in your C:\ drive.
2. Start Docker / Rancher Desktop.
3. In VScode, "Dev Containers: Open Folder in Container..." and select the directory you cloned this repo to.
4. Wait for the containers to build. The first time the containers build, this might take a while.
5. Open a terminal in your VScode devcontainer. Run `bash dev_setup.sh` and follow the instructions.
6. You can now run the server from the "Run and debug" sidebar in VScode or just run (from ./django) `python manage.py runserver` in the VScode terminal.
7. You will also have to start Celery to process tasks such as file translation or document loading. Run (from ./django) `celery -A otto worker -l INFO --pool=gevent --concurrency=16 -Q light,embed,heavy` or run the `Django: Server + Celery` debug configuration in VSCode. *Note that Celery requires a manual restart when files have changed.*
8. Go to http://localhost:8000 and login to Otto using your Justice account.

#### After initial setup

After the initial setup, you will rarely have to build the dev containers again. You can just:

1. Start Docker Desktop.
2. Open VScode, and if the devcontainer is not already opened, run "Dev Containers: Open Folder in Container..."
3. Start the Django server & Celery worker.

### With WSL Ubuntu

**Requirements:**
* Visual Studio Code (VScode) with Dev Containers extension
* WSL

_This assumes some knowledge of Linux / ability to troubleshoot. Not every step is explained in full detail._

1. Create or edit the file in your Windows user directory `.wslconfig`. The first two lines are essential when using Justice laptops. The other lines should be modified based on your system specifications.
```
[wsl2]
networkingMode=VirtioProxy
localhostForwarding=false
memory=8GB
processors=4
swap=10GB
```
Shutdown WSL to ensure it picks up the changes: `wsl --shutdown`
2. Install WSL Ubuntu. e.g. for Ubuntu 24.04 LTS, in PowerShell, run `wsl --install Ubuntu-24.04` then set as your default with `wsl -s Ubuntu-24.04`.
3. Open a Ubuntu terminal. Create a default user with sudo permissions. Follow the steps in the GitHub documentation to [add a SSH key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent).
4. Configure your shell (.zshrc or .bashrc) to auto-start ssh-agent:
```
   # Start SSH agent if not running
if [ -z "$SSH_AUTH_SOCK" ]; then
    eval "$(ssh-agent -s)"
    ssh-add ~/.ssh/id_ed25519  # or your key
fi
```
5. Start a new shell and, somewhere in your home directory, git clone the repo using SSH: `git clone git@github.com:justice-bac/otto.git`
6. In VScode, run command "WSL: Open Folder in WSL". Select the repo folder in your WSL filesystem. You may need to install the devcontainers extension in WSL VSCode.
7. You should be prompted to reopen the folder in a container. Do so, then continue from step 4 in the "With Docker or Rancher Desktop" instructions.

#### After initial setup

After the initial setup, you will rarely have to build the dev containers again. You can just:

1. Open VScode. If the devcontainer is not already opened, open the WSL folder, then "Reopen in container".
2. Start the Django server & Celery worker.

### Loading app data

All the commands you need to load data into Otto are in `django/initial_setup.sh`.

If you do not want to reset all your data (e.g. to preserve previously loaded Laws or Libraries), you can run individual commands.

For `reset_app_data`, you can specify the objects to reset. For example, to reset only the apps and groups data:

```bash
python manage.py reset_app_data apps groups
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

### Vendored browser assets

Otto now uses a small npm workspace to pin and vendor selected browser-side third-party assets (for example `docx-preview` and `jszip`) into Django's static asset tree.

From the repo root:

```bash
npm install
npm run vendor:sync
```

This updates the checked-in files under `django/otto/static/thirdparty/` using the versions pinned in `package-lock.json`.

### Load Legislation

For the legislation search app to function, we must load the XML files into the database.

You can use the management GUI from the user dropdown if you are an Otto admin.

  
### Celery scheduler

To enable the celery scheduler for local testing run the following command (from ./django):
```bash
celery -A otto beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Contributing

* Don't push commits directly to `main`.
* Always branch off `main` to create a feature branch, e.g. `git checkout -b chatbot-error-messages`
* Before opening a pull request (PR), make sure your branch is up to date with the source branch by running `git merge origin/main`; resolve any conflicts.
* Link your PR to an issue. If no issue exists, create one first to discuss the proposed changes.
* Run integration tests before opening a PR (see the instructions below). If the tests don't pass on your machine, they won't pass in the PR checks either.
* Write more tests if you have added new functionality (or are addressing a bug that wasn't previously caught by the tests).
* Use [conventional commits](https://kapeli.com/cheat_sheets/Conventional_Commits.docset/Contents/Resources/Documents/index) for commit messages and PR titles, e.g.:
  * `fix: chatbot not displaying errors` for a bug fix
  * `feat: upload document preview` for a new feature
  * `chore: upgrade llama-index version`
  * `refactor(librarian): extract document sync logic into utils`
  * `fix(chat_next): preserve tool-call streaming state`
* To indicate that the PR *isn't* ready to merge, create a `Draft PR`
* Get someone else to review your PR before merging it.

After your PR is merged:
  * On your workstation, `git checkout main` and `git pull`
  * Delete the branch that was just merged, e.g. `git branch -D chatbot-error-messages`
  * Create a new feature branch, if you are ready to do so.

### Pre-commit Hooks

Otto uses [pre-commit](https://pre-commit.com/) to automatically enforce code quality standards and maintain consistency across the codebase.

#### Installing Pre-commit Hooks

The pre-commit hooks are automatically installed when you run `bash dev_setup.sh`. If you need to install them manually:

```bash
pre-commit install
```

#### What Runs on Pre-commit

The following checks run automatically before each commit:

- **Black**: Python code formatter that ensures consistent code style
- **isort**: Organizes Python imports alphabetically and by section
- **djLint**: Formats and lints Django/Jinja templates for consistency
- **Terraform fmt**: Formats Terraform configuration files

If any of these checks fail, your commit will be blocked until you fix the issues. In most cases (Black, isort, and djLint), the tools will automatically fix the issues for you - just stage the changes and commit again.

#### Skipping Hooks (Not Recommended)

> [!WARNING] 
> Only skip hooks if you have a good reason and understand the implications.
In rare cases where you need to bypass these checks, you can use:

```bash
git commit --no-verify  # Skip pre-commit hooks
```

#### Manual Hook Execution

You can manually run the hooks at any time:

```bash
pre-commit run --all-files              # Run all pre-commit hooks on all files
pre-commit run black --all-files        # Run only Black on all files
```

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
python -m coverage run --source=django -m pytest django/tests; python -m coverage html; python -m coverage report
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

AGPL license due to PyMuPDF dependency.

For details, please see LICENSE.txt.

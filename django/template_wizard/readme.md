This tool is designed to facilitate the creation of dynamic and customizable documents by guiding users through a wizard-based process. Users can choose a wizard, select data, pick a template, and generate reports. The application's architecture supports multiple wizards, each tailored to specific scenarios, enabling flexibility and customization.

# Vision
The Template Wizard aims to empower users in effortlessly creating tailored documents by combining pre-defined templates with various data sources. Whether it's extracting information from case files, processing URLs, or generating answers from uploaded files, this application provides a versatile solution for document generation.

# Features

- **Workflow**: Intuitive steps guide users through wizard selection, data selection, template selection, and report generation.

- **Wizards**: Different wizards cater to different use cases, allowing users to choose the engine that best suits their needs. Common logic shared across engines facilitates mix-and-match flexibility.

- **AI Integration**: Seamlessly integrate AI services for tasks such as summarizatin, translation, and generating answers.

- **Save and Load**: Save and load reports for future reference, enabling users to revisit and modify reports as needed.

# Architecture

The application is built with a modular and extensible architecture. Key components include:

- **Report Model**: Stores information required for the user to generate and save a report, such as the wizard used, data selected, and template chosen.

- **Wizards**: Functionalities catering to specific scenarios, such as case file extraction, URL processing, and more.



# `{{ project_name }}`

{{ project_description }}

## Getting started

To start using this project, [first make sure your system meets its
requirements](#requirements).

It's suggested that you install this pack and its requirements within a virtual environment.

## Installing the package

Whilst in the root folder, in the command prompt, you can install the package and its dependencies using:

```shell
uv sync
```

This installs an editable version of the package. Meaning, when you update the
package code, you do not have to reinstall it for the changes to take effect.
(This saves a lot of time when you test your code)

Remember to update the pyproject.toml file inline with any changes to your
package dependencies. The initial files contain the bare minimum to get you started.

## Running the pipeline

The entry point for the pipeline is stored within the package and called `run_pipeline.py`.
To run the pipeline, run the following code in the terminal (whilst in the root directory of the
project).

```shell
uv run python src/{{ project_name }}/run_pipeline.py
```

Alternatively, most Python IDEs allow you to run the code directly from the IDE using a `run` button.

## Required environment variables

To run this project, you need a `.env` file with environment variables and secrets.
Copy `.env.example` to `.env` and fill in your actual values:

```shell
cp .env.example .env
```

For example:

| Variable | Description                                |
|----------|--------------------------------------------|
| `API_KEY` | Your API key for external services |

The `.env` file is ignored by git for security.


### Requirements

- Python 3.13+ installed
- a `.env` file with the [required environment variables](#required-environment-variables)

To install the contributing requirements, open your terminal and enter:
```shell
uv sync --group dev
```

## Pre-commit hooks

This project uses pre-commit hooks for code quality checks. After installing the dev dependencies, set up pre-commit:

```shell
uv run pre-commit install
```

This will automatically run code formatting, linting, and security checks before each commit. To run the checks manually:

```shell
uv run pre-commit run --all-files
```

## Acknowledgements

This project structure is based on the `chris-hyland-copier` template, which in turn is derived from `govcookiecutter`.

# Development Documentation

## Dev Setup

### Set up a Discord bot

First you need to go to discord developer portal to create a local dev bot and put its token into the .env file. (Bot → Token)

In installation:

- Disable user install
- Give it Scope: `application.commands` `bot`
- Give it Permissions: `Manage Roles` `Send Messages`

Give the bot “Server Members Intent”

### Install uv

See https://docs.astral.sh/uv/getting-started/installation/.

E.g. `pip install uv`.

### Install dependencies

`uv sync`

### Run the bot

First copy `/sample_env` to `.env` and add your discord token. Note that the Mailgun variables are optional, if you omit them the bot will print OTPs to stdout.

To start the bot run `uv run src/bot.py`.

Console output should be:

`Logged in as <your bot name>#XXXX`

Create a test server and invite the bot to the server (Under installation tab on discord dev portal)

Follow the instructions in `README.md` on how to initialise your test server.

### (Optional) Set up pre-commit hooks

Pre-commit hooks are managed by `prek`. They can be manually run with `uv run prek run`.

To install pre-commit hooks to automatically run before committing, run `uv run prek install`. This will ensure your commits are correctly formatted etc.

## Tooling

This repo uses [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/), [ty](https://docs.astral.sh/ty/), and [prek](prek.j178.dev).

To update dependencies, see the uv docs. If you just want to add a package you can `uv add <xyz>`.

To lint your code: `uv run ruff check`. To auto-fix run `uv run ruff check --fix`.

To format your code: `uv run ruff format`.

To type check your code: `uv run ty check`.

To lint, format, and type check staged files: `uv run prek run`.

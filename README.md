# Lists Assistant

![Lists Assistant](brand/icon.png)

Home Assistant integration for Listonic shopping lists. Version **1.0.0**,
domain **`lists_assistant`**. Requires Home Assistant **2026.2.3 or newer**.

English is the default language. Polish translations are included.

## Features

- A separate `todo` entity for each selected Listonic list.
- Two-way synchronization between one Listonic list and the built-in Home
  Assistant Shopping List, including standard Assist shopping commands.
- Create, rename, and deactivate lists; manage products, including quantity,
  unit, description, and price.
- Persistent offline queue, conflict detection, and recovery from uncertain writes.
- UI setup and reauthentication, a synchronization sensor, diagnostics, and
  English and Polish translations.

This is an independent community project that uses the Listonic app API. It is
not an official integration from the manufacturer; API changes may require updates.

## Install with HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/psorobka/lists_assistant` with the **Integration**
   category.
3. Find **Lists Assistant**, download the release, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, choose
   **Lists Assistant**, and sign in to Listonic.
5. Select the lists you want to expose as `todo` entities.

You can add the repository to HACS manually. Inclusion in the default HACS
catalog requires a separate submission and approval.

## Manual installation

Download the source archive from [GitHub releases](https://github.com/psorobka/lists_assistant/releases).
Copy `custom_components/lists_assistant` to
`/config/custom_components/lists_assistant` in Home Assistant, restart Home
Assistant, and add the integration through the UI.

## Shopping List and Assist

Enable the built-in **Shopping List** integration. During Lists Assistant setup,
choose a list to synchronize and confirm the initial merge. Every existing item
from both sides is kept, including items with identical names. One integration
can own the Shopping List. You can change the linked list in the options; queued
operations remain assigned to their previous target.

After linking the lists, you can use the Assist command “Add milk to the shopping
list”. See [setup, conflicts, and offline behavior](docs/shopping-list.md).
Actions are named `lists_assistant.get_lists`, `lists_assistant.add_item`, and
so on. See [action documentation and YAML examples](docs/actions.md).

## Development and verification

Run tests on Linux or WSL with Python 3.13 in a Linux virtual environment:

```sh
python -m pip install -r requirements_test.txt
ruff check --no-cache .
ruff format --no-cache --check .
python -m pytest -q --timeout=30 --cov=custom_components/lists_assistant --cov-report=term-missing --cov-fail-under=90
```

CI runs Ruff, pytest with a 90% coverage threshold, Chromium E2E, Hassfest, and
HACS checks. Automated tests use a fake Listonic server.
See [E2E setup and scope](docs/e2e.md), [release preparation](docs/releasing.md),
[changelog](CHANGELOG.md), and the [work map](docs/agent-workflow.md).

A full Home Assistant process restart, a long-running test, and UI
reauthentication remain separate verification areas. Historical tests against
the real API are described in the [live report](docs/api/live-validation.md);
the `scripts/live_*` scripts modify a real account and are not part of automated
release checks.

## Support and license

Report issues at [GitHub Issues](https://github.com/psorobka/lists_assistant/issues).
Include your Home Assistant and integration versions and an anonymized problem
description. Do not publish passwords, tokens, or `.storage` files.

[MIT License](LICENSE). API contract documentation: [docs/api](docs/api/README.md).

# AGENTS.md: exhibition-vm-controller

Engineering rules for all vmctl repos: https://docs.vmctl.org/contributing/engineering/
(source: `vmctl-docs/contributing/engineering.md`). They apply here in full: size limits,
structure, code style, tests, workflow. This file adds only what is specific to this repo.

Exhibition VM controller: runs finished works unattended on exhibition hosts.

## Gate

Run before every push. These are the commands of the CI `check` job; the pipeline is the authority.

```sh
cd host-controller
poetry check --lock
poetry install --no-interaction
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy vm_controller
poetry run pytest
cd .. && python3 scripts/check_sizes.py
```

## Repo specifics

- The Python package lives in `host-controller/vm_controller/`; run the Python gate from `host-controller/`.
- `vm_manager.py` and `api.py` are over the file limit and baselined. New features go into new modules, not into these files.

# Agent instructions

This repository contains a Home Assistant heatpump controller implemented as a PyScript app in `pyscript/apps/temptamer`.

## Repository-specific expectations

- Keep Home Assistant / PyScript runtime glue in `pyscript/apps/temptamer/main.py`.
- Keep decision logic in the pure-Python modules under `pyscript/apps/temptamer/` so it remains unit-testable outside Home Assistant.
- Update `tests/test_temptamer.py` when runtime decision logic changes.
- Validate changes with:
  - `python -m unittest discover -s tests -p 'test_*.py'`

## PyScript development guidance

- Follow the PyScript docs first:
  - Overview: https://hacs-pyscript.readthedocs.io/en/latest/
  - Reference: https://hacs-pyscript.readthedocs.io/en/latest/reference.html
  - Configuration: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#configuration
  - State variables: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#state-variables
  - Calling services: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#calling-services
  - Trigger decorators: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#function-trigger-decorators
  - Other decorators: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#other-function-decorators
  - Language limitations: https://hacs-pyscript.readthedocs.io/en/latest/reference.html#language-limitations
- Structure reusable PyScript apps as `pyscript/apps/<app_name>/__init__.py` packages with per-app config under `pyscript.apps.<app_name>`.
- Prefer `state.get()`, `state.getattr()`, and `service.call()` when entity names or service names are computed dynamically.
- Remember that PyScript reloads changed files automatically; don't add manual reload workflows unless they are actually needed.

## PyScript language limitations and constraints

From the upstream PyScript documentation:

- PyScript supports almost all Python language features except generators, `yield`, and defining special class methods such as `__init__` or `__str__`.
- PyScript functions are async by default. Use `@pyscript_compile` or `@pyscript_executor` only when native Python behavior is required, and remember compiled functions cannot use PyScript-specific features.
- Imports are restricted by default for security reasons unless `allow_all_imports` is enabled. Shared reusable code should normally live under `pyscript/modules`.
- Each PyScript file has its own global context. Do not assume globals defined in one file are directly shared with another file unless they are imported through a module.
- Home Assistant state values are strings. Cast them or use the helper conversion methods before numeric comparisons.

## When editing this repository

- Preserve the existing separation between configuration, state reading, demand resolution, zone control, dispatcher logic, and PyScript entrypoints.
- Prefer deterministic, side-effect-free helpers in the core modules and keep Home Assistant state/service access behind a narrow runtime boundary.
- Keep documentation and examples consistent with the concrete TempTamer entities described in `README.md` and `pyscript/apps/temptamer/config.py`.

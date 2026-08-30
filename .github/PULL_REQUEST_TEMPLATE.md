## What this changes

A short description of the change and why it is needed.

Closes #

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / cleanup
- [ ] Tests

## How it was tested

Describe what you ran and what you checked manually.

```
pytest
```

## Checklist

- [ ] `pytest` passes locally
- [ ] Tests added or updated for behavioural changes
- [ ] Docstrings and comments explain any non-obvious decision
- [ ] `python scripts/dump_schema.py` re-run if a model changed
- [ ] No `.env`, credentials or real uploads committed

## If this touches uploads or permissions

Confirm the project's invariants still hold (see CONTRIBUTING.md):

- [ ] Extension, MIME type **and** magic bytes are still all validated server-side
- [ ] Stored filenames are still server-generated, never user-supplied
- [ ] Uploads are still served only through the permission-checked route
- [ ] No filesystem path is exposed to users
- [ ] Image content still cannot set a complaint's category or priority

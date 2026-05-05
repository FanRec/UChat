# Identity Admin Module

Directory:

- [services/identity_admin](../../services/identity_admin)

## Responsibilities

- start challenges
- consume challenges
- manually bind accounts to persons
- rename canonical display names
- query identity state

## Startup

```powershell
uv run python -m services.identity_admin.main serve
```

## Dependencies

- `[identity_store]` in `config/app.toml`

If SQLite is used:

- the database file is created automatically on first startup
- the default path is `data/identity.sqlite3`

## When It Is Useful

- when multiple platform accounts should map to the same person
- when display names or person bindings need manual governance
- when you do not want identity correction logic hard-coded into the runtime main path

# Character and Identity Notes

The two concepts most often confused in the public repo are `runtime identity` and `identity store`.

## 1. runtime identity

Source:

- `config/app.toml` -> `runtime.identity`

This is a character prompt text block. It enters the LLM prompt and influences tone, style, and personality.

It is not the same thing as a user identity in the database, and it is not a fixed `person_id`.

The public repository only keeps an example identity. You should replace it with your own character definition.

## 2. identity store

Source:

- `config/app.toml` -> `[identity_store]`

Default example:

```toml
store_type = "sqlite"
sqlite_path = "data/identity.sqlite3"
default_console_person_id = ""
```

This means:

- the runtime uses a SQLite identity store
- the database file is created automatically if missing
- console input is not pre-bound to a fixed person because `default_console_person_id` is empty

## What Happens With Console Input

For console input:

- the platform is marked as `console`
- the input still goes through identity resolution
- but no fixed real person is automatically injected

So the default setup gives you:

- a character prompt
- no pre-bound real-world person identity

## When You Need `identity_admin`

Once you start merging real platform accounts, doing manual bindings, or cleaning up display names, `identity_admin` becomes useful. It governs identity data. It does not replace the character prompt in runtime.

# OData metadata cache must be flushed after schema changes

## TL;DR

FileMaker Server's OData service caches the schema (table definitions, field lists, validation rules, primary key composition) on startup and serves it to all clients. **When schema changes are made via FM Pro on a hot environment — adding fields, dropping fields, renaming, changing validation — the OData cache holds the old shape until the OData service is restarted.** Clients hitting OData during the gap see inconsistent behavior: writes silently fail, queries return the old field set, scripts called via `Script.{name}` POST return ERROR with confusing error messages that don't match what you see if you run the same script interactively in FM Pro.

Restart the FM Server OData service to flush the cache.

## Symptoms

| Symptom | Why |
|---|---|
| OData PATCH succeeds (HTTP 200) but the new field value isn't persisted | OData writes to its cached shape; the field doesn't exist there as a writable target |
| OData GET with `$select=NewField` returns no value for that column | New field is not in the cached metadata |
| `Script.{ScriptName}` POST returns `scriptResult: "ERROR"` with a generic wrapper message ("Could not create the inventory item ...") — but the same script in FM Pro Script Workspace returns OK | Script's sub-script writes a new field via Set Field; OData's session has the old schema and the write silently fails; script ERRORs on next field-write or validation |
| Test suite via `Script.{name}` POST returns mostly passes but a specific test fails — the failed test is the one that exercises the schema change | Schema change isn't visible to OData callers yet |
| Confusing error messages that don't reproduce in FM Pro Script Workspace | The script's logic is fine; the OData environment differs |

## How to fix

In FM Server Admin Console:

1. **Connectors → OData** (or equivalent depending on FM Server version)
2. **Disable** the OData service
3. Wait a few seconds
4. **Enable** the OData service
5. Re-run the affected client work — should now see the new schema

A full server restart works too, but it's heavier than necessary. The OData service restart is targeted.

## When to expect this

Any time you do **any** of the following via FM Pro on a hot environment:

- Add a new field to a table
- Drop a field
- Rename a field (FM auto-updates calc references but OData metadata is separately cached)
- Change a field's validation rules (e.g., adding `> 0`, changing strictness)
- Change a field's data type
- Add or change the table's primary-key composition (e.g., toggle the unique-value flag on `_id`, change uniqueness on another field — FM's OData treats every unique field as part of the composite key)
- Create or drop a value list (less commonly affects OData but worth flushing if value-list-driven scripts misbehave)

**Not** affected: data changes (record inserts, updates, deletes) — those flow through OData fine without restart.

## Verification after restart

Quick sanity check that the new shape is visible:

```bash
# Should show the new field, omit the dropped field, etc.
curl -s -u "$FM_USERNAME:$FM_PASSWORD" \
    "https://fmdev.special-lite.com/fmi/odata/v4/SL_Core/\$metadata" \
    | grep -A 5 "EntityType Name=\"YourTableName\""
```

If the metadata reflects the new schema, OData clients will see it on their next request.

## Why this isn't auto-handled

FileMaker's OData service was designed for read-mostly external integrations where the schema is stable. For development workflows that iteratively modify schema while clients are actively hitting OData, the cache is an operational hazard. The FM team has not (as of this writing) exposed a "flush metadata cache" command or auto-invalidation hook. The bounce-the-service workaround is the supported path.

## Practical pattern

When landing schema changes via the FM Pro UI on a hot environment, treat OData restart as part of the deploy:

1. Make schema changes in FM Pro
2. Bounce FM Server OData service
3. Run regression / integration tests via OData
4. (Optional) Bounce Data API service too if Data API clients are in scope — same caching behavior applies

If you're deploying ONLY data changes (record updates via OData PATCH or DELETE — no schema change), no restart needed.

## See also

- `agent/docs/knowledge/executesql.md` — `ExecuteSQL` performance and behavior on hosted vs local files
- `agent/docs/knowledge/record-locking.md` — related multi-user operational concerns
- `agent/docs/CLIPBOARD.md` → "Custom Function clipboard format" — another schema-modification gotcha (the OData composite-key issue from `_id` unique validation interacts with the metadata cache)

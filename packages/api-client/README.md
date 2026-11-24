# @anvesha/api-client

Typed client for the Anvesha API. `src/schema.d.ts` is generated from `openapi.json`,
which is exported from the backend. Regenerate both after changing the API:

```bash
cd backend && uv run python -m anvesha.api.openapi ../packages/api-client/openapi.json
cd .. && pnpm api:generate
```

CI fails if either file is out of date.

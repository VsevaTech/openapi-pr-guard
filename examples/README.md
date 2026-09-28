# Examples

| File | Relation to `openapi-v1.yaml` | Expected exit code |
|---|---|---|
| `openapi-v2-breaking.yaml` | required `phone`, `DELETE /v1/users/{id}` removed, `404` removed, `Company.address` removed, `Payment.amount` type changed, required `currency` param, `metadata` turned into `oneOf` | `1` |
| `openapi-v2-breaking-unversioned.yaml` | same as `openapi-v2-breaking.yaml`, but `info.version` left at `1.0.0` | `3` with `--no-fail-on-breaking --version-policy error` |
| `openapi-v2-compatible.yaml` | new endpoint, new method, optional params/properties, new response code, doc edits | `0` |

Security contract demo, relative to `security-base.yaml` (`POST /payments`: OAuth `payments:read`; `GET /payments/{id}`: OAuth **or** API key; `GET /payments`: inherits root security; `GET /health`: `security: []`):

| File | Change | Expected exit code |
|---|---|---|
| `security-breaking.yaml` | `POST /payments` also requires `payments:write`; `GET /payments/{id}` drops the API-key alternative; version `1.5.0` | `1`; `3` with `--no-fail-on-breaking --version-policy error` |
| `security-breaking-major.yaml` | same, version `2.0.0` | `1`; `0` with `--no-fail-on-breaking --version-policy error` |
| `security-compatible.yaml` | API key added as an alternative on `POST /payments`, scheme/scope descriptions edited | `0` |

```bash
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-breaking.yaml
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-compatible.yaml
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-breaking-unversioned.yaml \
  --no-fail-on-breaking --version-policy error
openapi-pr-guard --base examples/security-base.yaml --head examples/security-breaking.yaml
openapi-pr-guard --base examples/security-base.yaml --head examples/security-compatible.yaml
```

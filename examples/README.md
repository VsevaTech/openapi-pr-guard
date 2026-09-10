# Examples

| File | Relation to `openapi-v1.yaml` | Expected exit code |
|---|---|---|
| `openapi-v2-breaking.yaml` | required `phone`, `DELETE /v1/users/{id}` removed, `404` removed, `Company.address` removed, `Payment.amount` type changed, required `currency` param, `metadata` turned into `oneOf` | `1` |
| `openapi-v2-compatible.yaml` | new endpoint, new method, optional params/properties, new response code, doc edits | `0` |

```bash
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-breaking.yaml
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-compatible.yaml
```

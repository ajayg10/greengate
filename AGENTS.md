# Rules for agents working in this repository

GreenGate's principle: **agents decide, pipelines execute.** Agents read the repository and write commits,
merge requests and notes. Pipelines hold the cloud credentials (keyless OIDC) and do every deploy and merge.

## Allowed changes
- The greengate-optimizer flow may change only `.gitlab/ci/app.yml`.
- The phoenix-responder flow may change only `app/**`, `tests/**` and `postmortems/**`.
- Never change `.gitlab-ci.yml`, `.gitlab/ci/greengate.yml`, `greengate/**`, `infra/**`, `flows/**`, `agents/**`,
  `greengate.yml` or `LICENSE`. The `gg-policy` CI job rejects such merge requests.

## Tests are sacred
- Every distinct `pytest` command in `.gitlab/ci/app.yml` must keep running, character for character.
- Exact duplicate jobs may be removed. Never add `|| true`, `-k`, `--deselect`, `--lf` or `--maxfail`.

## Loop safety
- Every bot merge request carries the labels `greengate-auto` plus `greengate-optimizer` or `phoenix`.
- Branch names: `greengate/optimize-<pipeline id>` and `greengate/phoenix-<incident iid>`.
- If a merge request for the same branch is already open, do nothing.

## Style
- Conventional commits: `ci:`, `fix:`, `revert:`, `docs:`.
- Keep answers and notes short and factual. Label estimates as estimates. Never invent numbers.
- If unsure, make no change and explain why.

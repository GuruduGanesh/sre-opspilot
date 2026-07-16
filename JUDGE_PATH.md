# Judge path

OpsPilot's local review path uses a prebuilt checkout image so the controlled
Kubernetes scenarios do not need to be rebuilt from source. It does not require
an OpenAI API key: the two scripts verify the evidence, dry-run, approval, and
recovery gates. A GPT-5.6 key is required only to try the separate conversational
investigation endpoint.

## Scope and platform

This reviewer path demonstrates the controlled evidence and safety gates, not a
live GPT-5.6 investigation. A submitted hosted demo or recorded live demo must
show the separately configured investigation route after its API access has been
verified; judges must never be asked to provide their own OpenAI key.

The supplied scripts are verified only on Windows PowerShell 7+. No macOS or
Linux judge path is currently claimed. A hosted demo is the preferred way to make
the final reviewer experience platform-independent.

## Prerequisites

- Windows PowerShell 7+, Docker Desktop, `kind`, `kubectl`, Python 3.12, Node.js,
  and `uv`.
- The `opspilot-checkout-0.1.tar` artifact attached to the review release.

## Run the controlled review

```powershell
uv sync --all-groups
.\scripts\run-judge.ps1 -ImageArchive .\opspilot-checkout-0.1.tar
```

The command imports the supplied checkout image, creates only the dedicated
`opspilot-dev` kind cluster, and runs the P1 and P2 end-to-end safety flows. Each
flow intentionally triggers a failure, proves an unapproved action is denied,
applies an approved, dry-run-previewed allowlisted recovery, and independently
checks recovery. The scripts reset the controlled workload after the run.

## Package the review artifact

Maintainers create the ignored local package with:

```powershell
.\scripts\package-judge.ps1
```

Attach `artifacts/judge-package/opspilot-checkout-0.1.tar` to the GitHub release
used for review. Do not attach API keys, local databases, private plans, or
research archives.

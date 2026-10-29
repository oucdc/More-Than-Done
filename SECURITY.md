# Security and private data

## Reporting a problem

Do not include live API keys, personal conversations, project files, or full configuration dumps in public issues. Share a minimal reproduction with fake data. For a vulnerability, use the repository's private vulnerability-reporting feature if enabled; otherwise ask the maintainer for a private reporting channel without disclosing exploit details publicly.

## Credentials and records

The app can store an API key in plain text in `data/agent_config.json`, or read one from `.env`. Conversation records can include messages, submitted answers, local paths, and tool output. Back up private data securely and do not attach it to a release or source archive.

Only the empty `.env.example` template belongs in version control. Runtime `data` files, `.env` variants, virtual environments, build output, screenshots, logs, and private development notes must be excluded from public exports. An ignore rule does not remove a file already committed to Git history. If a key is exposed, revoke or rotate it with its provider and review all published artifacts and history before republishing.

## Runtime boundaries

- The HTTP interface listens on the local loopback address and checks request Host and Origin. It is intended for one local user, not for exposure as a public web service.
- The configured model provider receives conversation and tool context. Review the provider and endpoint before sending confidential material.
- Workspace file tools protect credential files, but approved shell commands run with the current operating-system user's permissions. Approval modes are not an OS sandbox. Full-access mode can reach files outside the selected workspace.
- A model reply is untrusted content. Check proposed commands and file changes before approving them, and independently verify important outputs and assessments.

## Publishing checks

Create releases from a clean source export and a dedicated release directory. Review the file manifest and scan for credentials before pushing. Do not publish the complete development folder, existing personal Git history, `.env`, or `data`. Verify that the release ZIP contains the executable and intended documentation only, and provide its SHA-256 checksum.

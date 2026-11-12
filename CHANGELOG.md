# PayPilot AI — update notes

## Windows/VS Code native launch audit

- Audited the full codebase for Ubuntu/WSL/Docker/Linux-only requirements: none were found. `setup.bat`/`run.bat` were already native Windows batch scripts with no shell-out to bash or WSL; `backend/app/core/config.py` already resolves every path with `pathlib` in a Windows-safe way; `requirements.txt` contains only packages with native Windows wheels.
- Corrected the README title, which still read "PayPilot AI V3 — Marketplace Stage 1" despite the project being the Unified Intelligence codebase (marketplace + auth + orchestrated AI assistant + fraud/risk + analytics). No other file had a mismatched project identity.
- Added `.vscode/tasks.json` (Setup / Run / Run tests tasks wrapping the existing `.bat` scripts), `.vscode/launch.json` (an F5 debug configuration for Uvicorn), and `.vscode/settings.json` (points the Python extension at `.venv`, enables pytest discovery) — these were genuinely missing.
- Added an "Open in VS Code" section to the README and updated `scripts/package_stage1.py` to bundle and verify the new `.vscode/` folder.
- Verification: `python3 -m py_compile` across every backend/scripts/tests `.py` file and `node --check` on `frontend/js/app.js` passed in the (Linux) development sandbox; the three new `.vscode/*.json` files were validated as well-formed JSON. **Not verified:** actual execution of `setup.bat`/`run.bat` or `pytest` on a real Windows machine, and no live Ollama/PayPal Sandbox calls — the development sandbox used to prepare this change has no Windows OS, no network access, and does not have this project's Python dependencies installed, so none of that could be executed here. Please run `setup.bat` then `run.bat` (or the equivalent VS Code tasks) on your own Windows machine and report back if anything fails.

## Unified Intelligence upgrade

- Added allowlisted, argument-validated assistant tools over the existing SQLite transaction, product, customer, order, cart, inventory, risk, analytics, and report data.
- Bound conversations to both their random session key and authenticated actor; stored follow-up tool context and response artifacts additively in SQLite.
- Enforced customer, merchant, and local administrator scopes in backend tool execution. Customer directory lookup is administrator-only; customer and merchant queries remain account-scoped.
- Added explicit customer-only PayPal Sandbox assistant operations for cart checkout creation, capture, and provider status verification. Checkout uses backend cart totals; capture checks provider amount/currency and remains idempotent.
- Kept local workspace intelligence independent from PayPal/Ollama availability. Deterministic answers identify the local fallback source; model explanations receive redacted, verified tool results only.
- Added product result images, table/report artifacts, and a validated PayPal Sandbox approval link in assistant chat.
- Updated `.env.example`, setup/security documentation, and the clean package builder. No credentials, local database, backups, or merchant uploads are bundled.
- Verification: `.venv\Scripts\python.exe -m pytest -q tests` — 49 passed (one Starlette/httpx deprecation warning); `node --check frontend/js/app.js` passed. Database initialization added zero transactions and products. SQLite integrity passed and pre-migration records matched the backup. PayPal and Ollama tests used mocked services; no live integration calls were run.
- Ollama tool selection and PayPal Sandbox order/capture/verification were exercised with mocked responses. No live Ollama model or PayPal Sandbox connection was run.


## Stage 1 Marketplace MVP

- Added an integrated dark-theme Marketplace with six product categories, 18 synthetic demo products, search, price filters, sorting, product detail, responsive shopping cart, and order history.
- Added customer and merchant registration/login using scrypt password hashing, expiring HttpOnly sessions, and API role and ownership checks.
- Added persistent SQLite cart, catalog, merchant, Marketplace order, and line-item snapshot models while retaining existing V3 assistant, transaction, risk, and PayPal data.
- Added backend-calculated USD totals, configurable tax and shipping, stock validation and reservation, cancellation/expiry release, and PayPal Sandbox checkout that never accepts browser-supplied amounts.
- Added merchant product publishing and owner-only price/stock updates. Replaced the legacy arbitrary-price PayPal modal with the cart-backed flow.
- Added Marketplace API tests for authentication, permissions, catalog search, prices/tax/shipping, stock, checkout, cancellation, and merchant ownership.
- Verification: `python -m pytest -q` — 35 passed (one upstream Starlette/httpx deprecation warning); `node --check frontend/js/app.js` passed. PayPal Sandbox calls are mocked; no live PayPal or Ollama calls were made.

## Fixes and additions

- Fixed the dynamically-created simulation form so its submit handler is attached after the modal is rendered.
- Clear active transaction filters after creating a simulation so the new record is visible immediately.
- Kept transaction creation, SQLite commit, and transaction list retrieval on the existing FastAPI/SQLAlchemy path.
- Added database-grounded customer-name initial lookup for explicit questions such as names beginning with S. The assistant returns matching synthetic names, transaction IDs, amounts, currencies, and statuses, but omits email addresses.
- Added grounded creator identity: H B Veeresha is identified as the project creator/developer, without inventing a company or CEO title.
- Added optional PayPal Developer Sandbox order creation, buyer approval redirect, capture, and local transaction recording. Credentials are server-side `.env` values only; the integration is disabled until configured.
- Added filtering of Qwen-style `<think>...</think>` segments so private reasoning tags are not shown in chat replies.
- Updated README and `.env.example` with PayPal Sandbox setup instructions.

## Verification

- `python -m pytest -q tests`: 29 passed.
- `node --check frontend/js/app.js`: passed.
- Python source compilation: passed.

PayPal Sandbox requires the user to create a Sandbox REST app and provide its Client ID and Secret in a local `.env` file. No PayPal credentials are included in this archive.

## Customer lookup fix

- AI Assistant customer searches now run as deterministic, administrator-authorized SQLite queries rather than relying on Ollama's intentionally redacted conversational context.
- Searches support first-letter, prefix, and partial-name matching across marketplace customer accounts and locally stored simulated transaction customers.
- Administrator results include available account, order, and transaction summaries; customer identifiers are not sent to Ollama for explanation.
- Customer and merchant permission boundaries remain enforced by the backend.

## Customer name-prefix query correction

- Recognize natural-language requests using "beginning with" and related prefix wording in the deterministic customer-directory search.
- Honor explicit "customer names only" requests by returning names without account IDs, email addresses, order details, or transaction totals.
- Added a regression test for the reported customer-name search wording.
- Verification: `python -m pytest -q` — 74 passed. `node --check frontend/js/app.js` and Python source compilation passed. PayPal Sandbox and live Ollama services were not contacted.

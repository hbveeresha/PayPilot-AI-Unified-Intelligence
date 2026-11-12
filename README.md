# PayPilot AI — Unified Intelligence

PayPilot AI is an integrated local marketplace and payment intelligence workspace. It combines a FastAPI service, SQLite transaction and marketplace records, explainable risk analysis, and a conversational assistant that uses a local Ollama model when available.

**Sandbox distinction:** local transaction and marketplace intelligence works without PayPal credentials or internet access. PayPal checkout is a separate Sandbox integration; local simulator records do not represent completed provider payments. Demo accounts and products are synthetic. Risk scores are review signals, not proof of fraud.

## Features

- Dark, responsive dashboard with live totals, recent transactions, insights, and activity charts.
- Integrated responsive Marketplace with synthetic demonstration products across electronics, mobiles, laptops, fashion, shoes, beauty, home, accessories, gaming, and appliances; includes local product photography, search, category/price filters, product details, Buy Now, a persistent cart, customer orders, and merchant product management.
- Customer and merchant accounts use scrypt password hashes, opaque HttpOnly session cookies, role checks, and account-owned carts, orders, and seller listings.
- Cart pricing is recomputed by the API from current product prices and stock. Tax, shipping, order snapshots, inventory reservation, and order totals are persisted in SQLite.
- Optional PayPal Sandbox checkout uses the validated server-side cart total; no amount is accepted from the browser. Inventory is reserved during approval and released on cancellation or expiry.
- Simulated approved, pending, and failed payment flows with unique order and transaction IDs.
- Optional PayPal Sandbox checkout flow: server-side OAuth, order creation, buyer approval, capture, and a local transaction record. Sandbox credentials stay in the backend environment.
- Transaction history with search, status and risk filters, pagination, details, and CSV export.
- Rule-based risk scoring with reasons and recommendations, plus Isolation Forest amount anomaly detection when Scikit-learn is installed.
- General-purpose AI assistant using a configurable local Ollama model for conversation, general knowledge, education, technical explanations, and multilingual chat. The chat streams model output as it is generated.
- The assistant detects English, Kannada, Hindi, Tamil, Telugu, and Malayalam scripts, recognizes common Kannada transliteration, follows natural language changes, and uses recent conversation turns for follow-ups.
- Payment and marketplace facts are calculated from SQLite through role-scoped backend tools, so the fallback never invents financial totals. When Ollama is unavailable, the UI labels the response as **Local fallback · limited understanding**; the fallback does not claim general language-model ability.
- Conversations are saved in SQLite and scoped to a random per-tab session key sent in an HTTP header. Start a fresh chat without losing previously saved history.
- The assistant uses a controlled allowlist of backend tools for transactions, products, customer/order scopes, inventory, carts, risk summaries, sales, and business reports. SQL and model-selected operations are never executed directly. Role checks are applied in each tool; customers and merchants are limited to their own records.
- Local database intelligence remains available when PayPal or Ollama is offline. Assistant answers identify their data source and label the limited deterministic fallback.
- Explicit signed-in customer requests can create a Sandbox checkout, capture an identified Sandbox order, or verify provider status. Checkout totals are recalculated from the saved cart. Capture verifies provider amount and currency before creating the internal transaction. The assistant does not initiate payment actions based on vague prompts; customers still approve checkout on PayPal.
- Customer names, email addresses, IP addresses, and card-like numbers are excluded from general AI context and redacted from conversation history sent to Ollama. Administrator-only customer-name prefix lookups are handled directly by SQLite and return only the requested name, transaction ID, amount, currency, and status; email addresses are omitted. General questions do not attach unrelated transaction records; SQLite data is queried only for recognized payment-data requests.
- Transaction filtering and search use a JSON `POST` body, keeping names and email search terms out of browser URLs.
- Analytics and daily/current-month reports sourced from SQLite records.
- Application settings for display currency, plus health and interactive OpenAPI docs.
- Windows setup and launch scripts; no Docker or paid API keys required.

## Architecture

```text
frontend/                 Single-page HTML/CSS/vanilla JS interface
backend/app/api/          FastAPI routes and request validation
backend/app/models/       SQLAlchemy relational models
backend/app/schemas/      Pydantic input/output schemas
backend/app/services/     Simulation, risk, assistant, and demo seed logic
backend/app/database/     SQLite engine and session lifecycle
data/                     Local SQLite file created at first run
tests/                    API and service tests using isolated in-memory SQLite
scripts/                  Database initialization helper
```

## Technology

- Python 3.11+
- FastAPI, SQLAlchemy 2, Pydantic Settings, SQLite
- Scikit-learn Isolation Forest and explainable rule checks
- Vanilla JavaScript, CSS, HTML, Chart.js
- Ollama local chat API (optional)
- PayPal REST API Sandbox (optional; requires PayPal Developer Sandbox app credentials)
- Pytest and HTTPX

## Windows setup

1. Install Python 3.11 or newer and enable **Add Python to PATH** in the installer.
2. Extract the project ZIP to a writable folder.
3. Double-click `setup.bat`. It creates `.venv`, installs dependencies, initializes SQLite, and inserts deterministic synthetic demo records.
4. Double-click `run.bat` and open [http://127.0.0.1:8000](http://127.0.0.1:8000).
5. API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

The first dependency install needs internet access. Ollama and PayPal Sandbox are optional; local simulation and database analytics work without either integration.

Nothing in this project requires Ubuntu, WSL, Docker, or a Linux terminal — `setup.bat`/`run.bat` are native Windows batch scripts, `backend/app/core/config.py` resolves all paths with `pathlib` (Windows-safe), and `requirements.txt` contains only packages with native Windows wheels (uvicorn's optional `uvloop` extra is skipped automatically on Windows by uvicorn's own packaging).

## Open in VS Code

1. In VS Code: **File → Open Folder…** and select the extracted project folder (the one containing `setup.bat`).
2. Install the Microsoft **Python** extension if you don't already have it (needed for the interpreter picker and the debugger).
3. Run setup once, either way:
   - Double-click `setup.bat` in File Explorer, **or**
   - In VS Code, open the Command Palette (`Ctrl+Shift+P`) → **Tasks: Run Task** → **PayPilot: Setup (first time only)**.
4. Once setup finishes, VS Code should automatically pick up `.venv` as the interpreter (configured in `.vscode/settings.json`). If it doesn't, click the Python version in the bottom status bar and choose `.venv\Scripts\python.exe`.
5. Start the app either way:
   - **Tasks: Run Task** → **PayPilot: Run** (runs `run.bat` in an integrated terminal), **or**
   - Press **F5** to start it under the debugger (`.vscode/launch.json`, breakpoints work in your route/service code).
6. Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with `Ctrl+C` in its terminal, or the Stop button if you launched it with F5.
7. To run the test suite from VS Code: **Tasks: Run Task** → **PayPilot: Run tests**, or use the Testing sidebar (pytest discovery is preconfigured).

If VS Code reports "unknown debug type debugpy" when you press F5, update the Python extension — current versions ship `debugpy` as the default debugger; older ones may need the configuration's `"type"` changed to `"python"`.

## Run from a terminal

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\init_db.py
$env:PYTHONPATH = "$PWD\backend"
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

For macOS/Linux, activate `.venv` with `source .venv/bin/activate`, set `PYTHONPATH=backend`, then run the same `uvicorn app.main:app` command.

## Ollama configuration

Install Ollama separately, pull a multilingual instruction model such as Qwen3 4B (`ollama pull qwen3:4b`), and ensure its local service is running. Qwen3 is available in several model sizes through the [Ollama Qwen3 library](https://ollama.com/library/qwen3). Copy `.env.example` to `.env` and set:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=45
```

The backend calls Ollama's local `/api/chat` endpoint. For recognized workspace questions, Qwen may select one of the role-filtered, allowlisted read tools; Python performs calculations and returns verified records, which Qwen can explain. General replies stream through `POST /api/assistant/chat/stream`; `POST /api/assistant/chat` remains available for non-streaming clients. The configurable timeout defaults to 45 seconds. If Ollama is unavailable, deterministic local tools still query SQLite, while the general-language fallback is intentionally limited and labeled. No model download or AI API key is required or bundled.


## Unified assistant data access

The assistant queries the same local SQLite database used by the application. It can summarize transactions, search records by status/amount/date/name prefix, search products, report inventory and cart totals, inspect permitted orders, summarize merchant sales, review risk assessments, and generate combined business reports. Transaction volume, successful volume, marketplace order value, and paid sales remain separate measures. Reports distinguish simulation records from PayPal Sandbox records.

Workspace data access follows the signed-in customer or merchant's role and account ID. The local administrator is available over loopback in development. For a remote deployment, set a strong server-only `WORKSPACE_ADMIN_TOKEN` and send it in the `X-Workspace-Admin-Token` header; never place it in frontend JavaScript. Customer accounts can access only their own transactions, carts, and orders; merchants are limited to their own inventory, order items, and sales. Customer account directory lookup is administrator-only. Passwords, emails, provider credentials, and authentication tokens are not sent to Ollama.

Schema updates for conversation ownership and tool-result metadata are additive. The startup migration preserves existing rows. Keep a separate backup before upgrading a valuable local database. A clean copy creates its local SQLite file and synthetic seed records on first run; personal databases and merchant uploads are not packaged.

## PayPal Developer Sandbox (optional)

Create a REST app under the **Sandbox** section of the PayPal Developer Dashboard. Copy its Sandbox Client ID and Secret into `.env`:

```dotenv
PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
APP_BASE_URL=http://127.0.0.1:8000
```

Restart PayPilot AI. Sign in to a customer account, add items in the Marketplace, and choose **Continue to PayPal Sandbox** from the cart. The backend rechecks stock and computes the total from its database before creating the PayPal order; the browser cannot submit a price. The Marketplace checkout and explicit assistant checkout actions use the same server-validated cart and Sandbox backend. Approval and capture are recorded against the customer’s Marketplace order in SQLite. Cancellation or expiry releases reserved stock. A customer may ask PayPilot AI to start checkout, capture a specific Sandbox order, or verify an existing provider order; these are explicit, signed-in customer actions, and the approval step remains on PayPal. Use a Sandbox personal buyer account. No live PayPal credentials or real payments are used. Do not commit `.env` or expose the secret in browser code.

## Marketplace accounts and pricing

The Marketplace is part of the existing application and shares its SQLite database with transaction and risk history. The initial catalog contains 24 synthetic demonstration products in 12 categories. All 24 seeded product photos are bundled locally under `frontend/images/products/`; their Pexels photo IDs and license links are listed in `frontend/images/ASSET_CREDITS.md`. These are representative stock photos for demo listings, not exact SKU photos or merchant endorsements. Use **Sign in** in the top bar to create a customer or merchant account; passwords must be at least 10 characters and are stored as scrypt hashes. The server issues an HttpOnly, SameSite session cookie. Customer sessions can save carts, place Sandbox orders, and view only their own order history. Merchant sessions can publish products, upload and preview JPEG/PNG/WebP product photos, and update price and stock only for products they own. Uploads are validated, limited to 5 MB by default, re-encoded to WebP, and stored under `data/product_uploads/`; those private uploads and local databases are excluded from the project ZIP. Role checks are enforced by the API.

The cart loads current catalogue prices from SQLite. Defaults are 8.25% tax, $5.99 shipping below $75, and free shipping at or above $75. Set `MARKETPLACE_TAX_RATE`, `MARKETPLACE_SHIPPING_FLAT`, and `MARKETPLACE_FREE_SHIPPING_THRESHOLD` in `.env` to adjust the local demo calculation. Checkout validates stock again and reserves it while the buyer is at PayPal. The default reservation expiry is 30 minutes (`MARKETPLACE_ORDER_TTL_MINUTES`). Orders keep price snapshots so later product price changes do not alter existing orders.

Startup adds the Marketplace tables and PayPal linkage columns to an existing SQLite database without removing the V3 assistant, transaction, or risk data. Back up a valuable database before upgrades. Demo catalogue rows are synthetic and safe for local development.

## API overview

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/api/health` | Service health and simulation mode |
| GET | `/api/dashboard` | Dashboard totals, recent activity, insights |
| GET | `/api/transactions` | Filter and paginate transaction history |
| POST | `/api/transactions/search` | Search/filter/paginate using a JSON request body |
| GET | `/api/transactions/{transaction_id}` | Transaction and risk details |
| POST | `/api/simulations` | Create an approved, pending, or failed local simulation |
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | Create, start, and end a customer or merchant session |
| GET | `/api/auth/me` | Get the signed-in account role and display name |
| GET | `/api/marketplace/categories` | List product categories |
| POST | `/api/marketplace/products/search` | Search, filter, and sort the public product catalogue |
| GET | `/api/marketplace/products/{slug}` | Read a product detail |
| GET/POST | `/api/marketplace/cart`, `/api/marketplace/cart/items` | Read the current account cart or add a product (customer only) |
| PUT/DELETE | `/api/marketplace/cart/items/{item_id}` | Update or remove an account-owned cart item |
| POST | `/api/marketplace/checkout/quote` | Validate and calculate the current cart total |
| POST | `/api/marketplace/checkout/paypal` | Create a PayPal Sandbox order using the backend-calculated cart total |
| GET | `/api/marketplace/orders` | List only the signed-in customer’s Marketplace orders |
| GET/POST/PUT | `/api/merchant/products` | List or manage products owned by the signed-in merchant |
| POST | `/api/merchant/products/{product_id}/image` | Upload a validated JPEG, PNG, or WebP photo to a merchant-owned product |
| POST | `/api/paypal/sandbox/orders` | V3-compatible checkout path; request body must be empty and cart-backed |
| POST | `/api/paypal/sandbox/orders/{paypal_order_id}/capture` | Capture an approved Sandbox order and record it locally |
| POST | `/api/assistant/chat` | Send a chat message and receive a complete response |
| POST | `/api/assistant/chat/stream` | Stream chat response chunks as newline-delimited JSON |
| GET | `/api/assistant/conversations/{conversation_id}` | Restore messages from one opaque conversation ID |
| DELETE | `/api/assistant/conversations/{conversation_id}` | Clear one conversation belonging to the supplied session header |
| GET | `/api/analytics` | Daily activity and status/risk distributions |
| GET | `/api/reports?period=daily\|monthly` | Daily or current calendar month report |
| GET | `/api/risk` | Explainable risk assessment feed |
| GET | `/api/export/transactions.csv` | Download the local transaction history |
| GET/PUT | `/api/settings` | Read/update display currency preference |

The assistant endpoints require an `X-Conversation-Session` header containing the current tab’s 32-character random session token. Marketplace authentication uses the browser’s HttpOnly cookie; passwords and other private fields are sent in request bodies, never URL parameters. The full interactive API reference is served by FastAPI at `/docs`; the OpenAPI schema is at `/openapi.json`. Search criteria containing customer data should be sent only to the POST search endpoint. Do not add personal information to URL query parameters.

## Tests

Run the API and service tests with:

```powershell
python -m pytest -q tests
```

The test fixture uses an isolated in-memory SQLite database and deliberately skips application startup seeding so it does not touch your local demo database. Photo-upload tests write only temporary normalized images and clean them up; seeded photo tests verify local assets are readable through the static route. Tests cover existing dashboard, assistant, risk, and transaction behavior; general and multilingual fallback responses; spelling variants; database-grounded payment facts; follow-ups and session separation; Ollama-unavailable fallback; privacy-safe POST URLs; authentication and role checks; catalogue search; server cart calculations; stock reservation and release; checkout order creation; and invalid inputs. PayPal and Ollama integrations are mocked/offline in tests; configure Sandbox credentials and a local Ollama model separately to exercise those services manually.

## Screenshots

No screenshots are bundled yet. Start the app and capture the dashboard at `/` if you need a project preview.

## Troubleshooting

- **`python` or `py` not found:** install Python 3.11+ and reopen the terminal; rerun `setup.bat`.
- **Port 8000 is in use:** stop the other process or change the port at the end of the `uvicorn` command in `run.bat`.
- **Assistant replies with fallback:** verify Ollama is running, the model is pulled, and the URL/model in `.env` match your local configuration.
- **Charts do not load:** Chart.js is loaded from jsDelivr; the dashboard still provides a simple canvas fallback when the library is unavailable. Network access is needed for the web font and full Chart.js rendering.
- **Database reset:** stop the server, back up any data you want to keep, then remove `data/paypilot.db`; the next launch initializes and seeds a fresh local database.

## Project limitations

- Local simulation records are synthetic. The optional PayPal integration connects only to PayPal Sandbox; it does not connect to live payment processing, banks, or card networks.
- Risk rules and unsupervised anomaly detection are educational heuristics and are not validated for real fraud prevention.
- Marketplace customer and merchant routes have account-level ownership checks. The original simulator, analytics, and risk workspace remains a local educational admin experience rather than a production multi-tenant deployment. The server binds to localhost in the provided Windows launcher.
- Ollama responses are local model output and may be incomplete. The local fallback covers a narrow set of phrases and database intents; it does not provide the model's general multilingual understanding.
- Currency selection changes display formatting only; it does not perform exchange-rate conversion.
- Chart.js and web fonts use CDN resources; the interface remains usable if the network is unavailable.

## License

Released under the MIT License. See [LICENSE](LICENSE).

# Arctos

Centralized online results and event management for Jugger.  

Or, *what the fog site always wanted to be*  

see [CONTRIBUTING](CONTRIBUTING.md) for how to get involved.

## Running the App

### Part 1: Backend

1. Install [uv](https://docs.astral.sh/uv/).
2. Set up your SSL certs. If you're using nginx you can do this there
   and use [certbot](https://certbot.eff.org/). If you're just testing, you can
   generate self-signed certs with:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -sha256 -days 365
```

or

```bash
make certs
```

   This writes `cert.pem` and `key.pem` to the repo root (valid for
   365 days, `CN=localhost`). Override with
   `make certs CERT_DAYS=730 CERT_SUBJECT=/CN=arctos.example.com`,
   and pass `FORCE=1` to overwrite existing certs.

3. Create a `.env` file at the repo root with the variables you need:

```bash
ARCTOS_CORS_DEV=1
ARCTOS_API_BASE=http://127.0.0.1:8081
EXTERNAL_BASE_URL=your_public_domain_or_ip
YOUTUBE_API_KEY=your_youtube_api_key
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_CLIENT_ID=your_google_client_id
SECRET_KEY=your_app_secret_key
```

If you don't have some of these, you can leave them empty; they are
only needed for the sign in with google and youtube auto-seek
features. The `SECRET_KEY` variable must be a random value for
security reasons. You can get one by running

```bash
python -c "import os; print(os.urandom(12).hex())"
```

> [!IMPORTANT]
>
> The `ARCTOS_CORS_DEV` and `ARCTOS_API_BASE` are only for dev
> environments where you don't have a reverse proxy set up to direct
> traffic and are thus hosting the frontend and backend on different
> ports.

4. Start the app:

```bash
make run
```

This loads `.env`, runs `uv sync`, and starts gunicorn. The defaults
match the example above (5 workers, binding `0.0.0.0:8081`, using
`cert.pem`/`key.pem`). Override any of them on the command line, e.g.:

```bash
make run WORKERS=10 BIND=0.0.0.0:9000
make run CERTFILE= KEYFILE=          # if you handle SSL elsewhere
make run ENV_FILE=.env.prod          # use a different env file
```

### Schema migrations

Arctos now ships app-managed bootstrap migrations. Startup runs migrations
automatically, and you can also run them explicitly:

```bash
make migrate
```

This is idempotent and safe to run repeatedly.

#### Video storage

To store finalized match recordings in an s3 compatible bucket (I use
Backblaze B2) instead of local disk, set these environment variables
in your `run` script:

| Variable | Required | Description |
|----------|----------|-------------|
| `S3_VIDEO_BUCKET` | Yes | bucket name (create a private bucket in the B2 dashboard). |
| `S3_ENDPOINT_URL` | Yes (for B2) | B2 S3-compatible endpoint, e.g. `https://s3.us-west-002.backblazeb2.com`. Use the endpoint for the region where you created the bucket. |
| `AWS_REGION` | Yes (for B2) | Must match the endpoint region, e.g. `us-west-002` or `us-east-005`. |
| `AWS_ACCESS_KEY_ID` | Yes | Application Key ID. Needs R/W access. |
| `AWS_SECRET_ACCESS_KEY` | Yes | corresponding secret key |
| `S3_PRESIGNED_EXPIRY_SECONDS` | No | Presigned URL lifetime in seconds (default `3600`). |

### Part 2: Frontend

Install frontend dependencies:

```bash
make setup
```

This installs system packages including Rust/Cargo. Then install the Dioxus CLI:

```bash
cargo install dioxus-cli
```

On Ubuntu, `make setup` installs apt Rust/Cargo and auto-upgrades to rustup
stable if the apt Cargo version is too old for Edition 2024 crates.

then (for development) simply `cd frontend` and serve the app:

```bash
dx serve
```

In production, you should run `dx bundle --release` and copy the
output files to somewhere that your reverse proxy can serve.

For a quick frontend compile check:

```bash
cd frontend
cargo check
```

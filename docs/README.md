# Building the Documentation

The documentation is built using [Sphinx](https://www.sphinx-doc.org/) inside a Docker container so that no local Python environment setup is required.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) must be installed and running.
- `make` must be available (standard on Linux/macOS; on Windows use WSL or Git Bash).

## Building

From this directory (`docs/`), run:

```bash
make html
```

This does two things automatically:

1. **Builds the Docker image** (`arctos-sphinx`) from `build_system/Dockerfile` if it does not already exist. The image contains Sphinx and all required extensions.
2. **Runs `sphinx-build`** inside that container with the repository root bind-mounted, producing HTML output at `docs/_build/html/`.

Open `docs/_build/html/index.html` in a browser to view the result.

## Other Targets

| Target        | Description                                      |
|---------------|--------------------------------------------------|
| `make html`   | Build (or rebuild) the Docker image and generate HTML docs |
| `make image`  | Build the Docker image only, without running a doc build |
| `make clean`  | Delete the `_build/` output directory            |

## Rebuilding the Docker Image

The Docker image is only built once and then cached by Docker. If you change `build_system/Dockerfile` (e.g. to add a new Sphinx extension), force a rebuild with:

```bash
make image && make html
```

Or remove the cached image manually and re-run `make html`:

```bash
docker rmi arctos-sphinx
make html
```

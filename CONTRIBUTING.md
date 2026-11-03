# Contributing

Report bugs and propose features through [GitHub issues](https://github.com/reid23/arctos/issues).
For a bug, include the route, steps to reproduce it, expected behavior, and
browser/device details. Remove private information from screenshots and logs.

## Development workflow

1. Start from the current `dev` branch. For a substantial change, agree on the
   problem and scope in an issue before implementing it.
2. Follow the [local setup guide](README.md#running-the-app-locally). Keep
   development and test data separate from production.
3. Implement the change with tests that exercise its behavior. Prioritize
   permissions, validation, persistence, failure recovery, and regressions.
   A wording or cosmetic change usually needs review rather than a new test.
   Follow the [value-handling conventions](app/README.md#error-values) for
   optional values, expected failures, and boolean conditions.
4. Run the relevant checks from [TESTING.md](TESTING.md). Format Python with
   `just format`; use Rust formatting for changed Rust code. Avoid unrelated
   formatting changes.
5. Open a pull request to `dev`. Explain the problem, resulting behavior,
   validation performed, and any remaining limitations. Include migration and
   deployment steps when required.

Use conventional commit subjects, for example
`fix: retain registration draft after a failed save`.

CI runs backend tests and coverage, Python lint/format and type checks, frontend unit
tests, and the WebAssembly compile check. Changes affecting interactions also
need browser review; passing a compile check does not verify layout or focus.

Maintainers review changes on `dev` before promoting them to `main`. For
schema changes, document the intended database and backup procedure in
[migrations/README.md](migrations/README.md).

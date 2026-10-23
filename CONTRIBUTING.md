# Contributing

yes yes yes please help!!! there are nowhere near enough hands on this
and any help you'd like to provide would be greatly appreciated.

### Bug Reports & Feature Requests

If you aren't a developer but would like to report a bug or request a
feature, feel free to do so by creating a new
[issue](https://github.com/reid23/arctos/issues) describing your
bug/feature. Select the Bug Report or Feature Request templates as
appropriate when it prompts you.

### Writing Code

Im not going to write much here; there's no reason Arctos needs to be
different from most other open source projects.

If you want to make your own version, fork it and go ahead. do
whatever you like.

If you'd like changes merged back into the main version, the process
works like this (assuming the staging system/dev server/dev branch
stuff is all set up):
0. a bug report or feature request issue is created
1. a Project issue is created, tagging one or more bug/feature request
   issues, describing a solution
2. a branch is created to work on the project.
    - branch names should follow the pattern `category/name`.
      categories are `feat` (feature), `bugfix`, or `refactor`.
    - branch off the latest `dev` (the staging branch)
3. Implementation
    - Always write tests. See [`TESTING.md`](TESTING.md)
    - run `just test` (or `uv run pytest tests/`) to make sure all
      tests pass before submitting
4. PR to `dev` submitted
    - describes everything that changed and any potential high level
      system impacts
    - documentation and PR descriptions must stand on their own: explain
      Arctos behavior and code using references other developers can access,
      without relying on private conversations or unpublished materials.
    - describes any migration changes needed!!
    - make sure there are no merge conflicts before submitting. i dont
      care if you merge or rebase.
    - all tests must pass before the PR is merged (`just test`)
    - **important:** all code must be formatted with [ruff](https://docs.astral.sh/ruff/)
5. maintainer approves PR and changes go live to dev server
6. final testing on dev server to ensure everything works (mostly
   important for changes requiring nonzero migration effort)
7. `dev` is PR'd regularly to `main`. (frequency tbd; likely as needed
   based on how many new features are in dev, when they're needed,
   when downtime is acceptable if needed, etc.)
    - PR lists features included and describes in detail the migration
      process if one is needed.

Please do note that **this code is not pretty right now**. If you see
something and think "why the hell is this the way it is?", you're not
crazy, it's just bad code. Please rewrite if you feel so inclined. In
general, this codebase has a worrying shortage of:
- tests
- proper docstrings for everything that work with sphinx
- non-stupid code
- input validation
- explanatory bits of text for users
- sensible timezone handling
- and more...

the TLDR here is that this codebase is very much still moving out of
the prototype phase and so a) i'd like to keep the standards for new
code higher than the quality of the existing code, and b) if you see
something horrible, i'd love you forever if you helped fix it.

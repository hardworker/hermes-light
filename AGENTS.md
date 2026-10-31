# Repository guidance

Hermes Light packages the remote client and its Homebrew cask. Apply these
conventions to code, workflows, tests, and documentation throughout this repo.

## Readability

- Optimize for the next maintainer reading the code. Prefer a straightforward
  sequence of small, explicit operations over clever shortcuts or abstractions.
- Use descriptive names that explain intent. Avoid unexplained abbreviations
  and single-letter names outside short, obvious loops.
- Write conditionals, loops, exception handling, and function bodies on multiple
  lines. Do not compress branches onto one line or join statements with semicolons.
- Break long calls, conditions, and data structures across lines. Use named
  intermediate values when a nested expression takes effort to understand.
- Aim for lines of about 100 characters. Keep immutable hashes and URLs intact.
- Separate logical stages with blank lines. Keep each function focused on one
  purpose, and extract helpers when they make the flow easier to follow.
- Explain non-obvious decisions and constraints in comments; use clear code for
  the operation itself.
- Follow these conventions in new code and when editing existing code. Keep
  readability improvements focused on the code touched by the task.

## GitHub workflows

- Give every step a meaningful name and leave a blank line between steps.
- Use expanded YAML lists and multiline expressions for long conditions.
- Use descriptive environment variable names and multiline shell control flow.
- Keep the workflow readable from top to bottom. Put substantial or repeated
  logic in scripts, without adding unnecessary layers or recovery machinery.
- Pin every external action to a full commit SHA, with its exact release version
  in a comment. Resolve updates from the official repository's latest stable
  release and review compatibility notes before changing the pin.

## Validation

- Run `make test` for changes to release or update behavior.
- Validate changed workflows with `actionlint` and run
  `python3 scripts/check_repository.py --history` before publishing changes.
- Use tests for meaningful behavior and failure cases; avoid tests that merely
  assert formatting or repeat the implementation.

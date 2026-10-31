# Repository guidance

Apply throughout this repository:

- Use descriptive names, focused functions, and straightforward control flow.
- Write branches and loops on multiple lines. Wrap long calls and conditions.
- Separate stages with blank lines; comment on non-obvious decisions.
- Aim for 100-character lines, keeping hashes and URLs intact.
- Name workflow steps, space them apart, and expand long YAML expressions.
- Pin actions to verified latest stable commit SHAs with exact version comments;
  review compatibility notes.
- Keep changes focused. Test behavior and failure cases, not formatting.

Before publishing, run `python3 scripts/check_repository.py --history`.
For behavior changes, run `make test`; for workflow changes, run `actionlint`.

# mkdocs-filter

**Filter mkdocs output to show only what matters: warnings and errors.**

## Before & After

<div class="comparison">
<div class="comparison-item">
<div class="comparison-header bad">Raw mkdocs output ✗</div>

```
INFO    -  Cleaning site directory
INFO    -  Building documentation to directory: /path/to/site
INFO    -  Doc file 'index.md' contains a link 'img.png'...
INFO    -  Doc file 'guide.md' contains a link 'old.md'...
INFO    -  Doc file 'api.md' contains an unrecognized...
INFO    -  Doc file 'tutorial.md' contains a link...
INFO    -  Doc file 'reference.md' contains a link...
WARNING -  markdown_exec: Execution of python code block
           exited with errors

Code block is:

  import numpy as np
  from mypackage import process_data

  data = np.random.rand(100, 100)
  result = process_data(data)
  print(f"Result shape: {result.shape}")

  # This will fail
  raise ValueError("INTENTIONAL TEST ERROR")

Output is:

  Traceback (most recent call last):
    File "<code block: session test; n1>", line 8, in <module>
      raise ValueError("INTENTIONAL TEST ERROR")
  ValueError: INTENTIONAL TEST ERROR

INFO    -  Doc file 'changelog.md' contains a link...
INFO    -  Doc file 'contributing.md' contains a link...
WARNING -  [git-revision] Unable to read git logs for
           'docs/new-page.md'. Is git installed?
INFO    -  Doc file 'faq.md' contains a link 'old.md'...
INFO    -  Doc file 'install.md' contains a link...
WARNING -  Documentation file 'missing.md' contains a
           link 'nonexistent.md', but the target is
           not found among documentation files.
INFO    -  Building search index...
INFO    -  Documentation built in 21.54 seconds
```

</div>
<div class="comparison-item">
<div class="comparison-header good">Filtered output ✓</div>

```
Built in 21.54s


⚠ WARNING [markdown_exec] ValueError: INTENTIONAL TEST ERROR
   📍 session 'test' → line 8

╭─────────────────── Code Block ───────────────────╮
│   1 import numpy as np                           │
│   2 from mypackage import process_data           │
│   3                                              │
│   4 data = np.random.rand(100, 100)              │
│   5 result = process_data(data)                  │
│   6 print(f"Result shape: {result.shape}")       │
│   7                                              │
│   8 raise ValueError("INTENTIONAL TEST ERROR")   │
╰──────────────────────────────────────────────────╯
╭────────────── Error Output ──────────────╮
│ ValueError: INTENTIONAL TEST ERROR       │
╰────────── use -v for full traceback ─────╯

⚠ WARNING [git-revision] Unable to read git logs
   📍 docs/new-page.md

⚠ WARNING Documentation file 'missing.md' contains
   a link 'nonexistent.md', but target not found

────────────────────────────────────────
Summary: 3 warning(s)

Built in 21.54s

Hint: -v for verbose output, --raw for full mkdocs output
Tip: Use mkdocs build --verbose to see source files
```

</div>
</div>

## Install

```bash
uv tool install mkdocs-filter
```

## Use

```bash
mkdocs build 2>&1 | mkdocs-output-filter
mkdocs serve --livereload 2>&1 | mkdocs-output-filter
```

## Features

| Feature | Description |
|---------|-------------|
| **Filtered output** | Only shows warnings and errors |
| **Code blocks** | Syntax-highlighted code that caused errors |
| **Location info** | File, session, and line number |
| **Streaming mode** | Real-time output for `mkdocs serve` |
| **Interactive mode** | Toggle raw/filtered with keyboard |
| **MCP server** | API for AI code assistants |

## Options

```
-v, --verbose      Show full tracebacks
-e, --errors-only  Hide warnings, show only errors
--no-color         Disable colored output
--raw              Pass through unfiltered
-i, --interactive  Keyboard toggle mode
```

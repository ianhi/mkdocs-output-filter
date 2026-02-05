# mkdocs-filter

**Filter mkdocs output to show only what matters: warnings and errors.**

## Before & After

<div class="comparison">
<div class="comparison-item">
<div class="comparison-header bad">Raw mkdocs output ✗</div>

```
INFO    -  Cleaning site directory
INFO    -  Building documentation to directory: /path/to/site
INFO    -  Doc file 'index.md' contains a link 'img.png', but the target is not found
INFO    -  Doc file 'guide.md' contains a link 'old-page.md', but the target is not found
INFO    -  Doc file 'api.md' contains an unrecognized relative link 'ref.md#section'
INFO    -  Doc file 'tutorial.md' contains a link '../images/fig1.png', target not found
INFO    -  Doc file 'reference.md' contains a link 'deprecated.md', target not found
INFO    -  Doc file 'examples.md' contains a link 'data/sample.csv', target not found
INFO    -  Doc file 'advanced.md' contains a link 'legacy/old.md', target not found
INFO    -  Doc file 'setup.md' contains a link 'requirements.txt', target not found
INFO    -  Doc file 'config.md' contains a link 'schema.json', target not found
INFO    -  Doc file 'plugins.md' contains a link 'hooks.py', target not found
INFO    -  Doc file 'themes.md' contains a link 'custom.css', target not found
INFO    -  Doc file 'extensions.md' contains a link 'macros.py', target not found
INFO    -  Doc file 'deployment.md' contains a link 'docker-compose.yml', not found
INFO    -  Doc file 'testing.md' contains a link 'fixtures/', target not found
INFO    -  Doc file 'migration.md' contains a link 'upgrade-guide.md', target not found
INFO    -  Doc file 'changelog.md' contains a link 'releases/v1.md', target not found
INFO    -  Doc file 'contributing.md' contains a link 'CODE_OF_CONDUCT.md', not found
INFO    -  Doc file 'faq.md' contains a link 'troubleshooting.md', target not found
INFO    -  Doc file 'install.md' contains a link 'binaries/', target not found
INFO    -  Doc file 'security.md' contains a link 'SECURITY.md', target not found
INFO    -  Doc file 'performance.md' contains a link 'benchmarks/', target not found
INFO    -  Doc file 'integrations.md' contains a link 'partners.md', target not found
INFO    -  Doc file 'roadmap.md' contains a link 'milestones.md', target not found
INFO    -  Doc file 'support.md' contains a link 'contact.md', target not found
INFO    -  Doc file 'license.md' contains a link 'NOTICE', target not found
INFO    -  Doc file 'credits.md' contains a link 'contributors.md', target not found
WARNING -  markdown_exec: Execution of python code block exited with errors

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

WARNING -  [git-revision-date-localized-plugin] Unable to read git logs for 'docs/new-page.md'. Is git installed?
WARNING -  Documentation file 'docs/guide/missing.md' contains a link 'nonexistent.md', but the target 'nonexistent.md' is not found among documentation files.
INFO    -  Building search index...
INFO    -  Search index built successfully
INFO    -  Copying static assets...
INFO    -  Static assets copied
INFO    -  Documentation built in 21.54 seconds
```

</div>
<div class="comparison-item">
<div class="comparison-header good">Filtered output ✓</div>

<div class="terminal">
<span class="dim">Built in </span><span class="cyan">21.54</span><span class="dim">s</span>

<span class="yellow">⚠ WARNING</span> <span class="dim">[markdown_exec]</span> ValueError: INTENTIONAL TEST ERROR
<span class="cyan">   📍 session </span><span class="green">'test'</span><span class="cyan"> → line </span><span class="cyan-bold">8</span>

<span class="cyan">╭─────────────────── Code Block ───────────────────╮</span>
<span class="cyan">│</span>   <span class="line-num">1</span> <span class="keyword">import</span> numpy <span class="keyword">as</span> np                           <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">2</span> <span class="keyword">from</span> mypackage <span class="keyword">import</span> process_data           <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">3</span>                                              <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">4</span> data = np.random.rand(<span class="number">100</span>, <span class="number">100</span>)              <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">5</span> result = process_data(data)                  <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">6</span> <span class="builtin">print</span>(<span class="string">f"Result shape: {result.shape}"</span>)       <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">7</span>                                              <span class="cyan">│</span>
<span class="cyan">│</span>   <span class="line-num">8</span> <span class="keyword">raise</span> <span class="exception">ValueError</span>(<span class="string">"INTENTIONAL TEST ERROR"</span>)   <span class="cyan">│</span>
<span class="cyan">╰──────────────────────────────────────────────────╯</span>
<span class="red">╭────────────── Error Output ──────────────╮</span>
<span class="red">│</span> ValueError: INTENTIONAL TEST ERROR       <span class="red">│</span>
<span class="red">╰────────── use -v for full traceback ─────╯</span>

<span class="yellow">⚠ WARNING</span> <span class="dim">[git-revision]</span> Unable to read git logs
<span class="cyan">   📍 docs/new-page.md</span>

<span class="yellow">⚠ WARNING</span> Link target not found
<span class="cyan">   📍 docs/guide/missing.md → nonexistent.md</span>

<span class="dim">────────────────────────────────────────</span>
Summary: <span class="yellow">3 warning(s)</span>

<span class="dim">Built in </span><span class="cyan">21.54</span><span class="dim">s</span>

<span class="dim">Hint: </span><span class="dim">-v</span><span class="dim"> for verbose, </span><span class="dim">--raw</span><span class="dim"> for full output</span>
</div>

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

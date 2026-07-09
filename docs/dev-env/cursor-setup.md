# Cursor Setup (Editor, Autocomplete, Rules)

!!! abstract "Goal of this page"
    By the end you'll have Cursor editing your automation repo, with **working
    autocomplete against the Civil 3D API**, linting that catches the
    [gotchas](../gotchas.md), and the house `.cursorrules` loaded so Cursor's AI writes
    code to our standard.

    🔀 = a spot where settings differ on non-2025 Civil 3D.

---

## Step 1 — Install Cursor and the Python extension

1. Download and install **Cursor** from [cursor.com](https://cursor.com).
2. Open Cursor → open your **automation repo folder** (`File → Open Folder`).
3. Install the **Python** extension (Cursor uses the open-VSX/VS Code Python +
   Pylance-equivalent language server). Extensions panel → search "Python" → install.

!!! tip "Open the folder, not a single file"
    Autocomplete, linting, and `.cursorrules` only work when Cursor has the whole
    **workspace** open. Open the repo folder, not a lone `.py`.

---

## Step 2 — Point Cursor at the *right* Python

Dynamo's CPython 3 engine is a **specific Python version**. Matching Cursor's
interpreter to it avoids "works in Cursor, breaks in Dynamo" surprises.

**Civil 3D 2025 / Dynamo 3.x ships CPython 3.8-3.9-class engine (`pythonnet`).**
🔀 Older Dynamo (2.x, Civil 3D 2023-2024) used **Python 3.8** too but via a slightly
different `DSIronPython`/`PythonNet2` split — check `Dynamo → Preferences → Python`
for the exact version your install reports.

You don't need to *run* Python locally, but selecting a matching interpreter gives
Pylance correct standard-library behaviour:

1. Install a local **Python 3.9** (from python.org) if you don't have one.
2. In Cursor: `Ctrl+Shift+P` → **Python: Select Interpreter** → pick 3.9.

!!! note "Why local Python at all if Civil 3D runs it?"
    Purely for the language server: it needs *a* Python to resolve `import math`,
    type-check, and lint. It never executes your Civil 3D code. Match the **major.minor**
    version to what Dynamo reports so f-strings, walrus, etc. behave identically.

---

## Step 3 — Install Civil 3D API type stubs (this is what powers autocomplete)

The Civil 3D API is .NET. Cursor can't introspect the live DLLs, so we feed it
**type stubs** — `.pyi` files describing the API's classes and methods.

### Option A — Community stubs (fastest start)

Several community stub packages exist for AutoCAD/Civil 3D .NET. Install into a
`typings/` folder that Cursor reads:

```bash
# from your repo root
mkdir typings
pip install autocad-stubs --target ./typings      # AutoCAD core (Autodesk.AutoCAD.*)
```

🔀 Community stubs track specific releases loosely. They cover the *core* AutoCAD
namespaces well; **Civil-specific** namespaces (`Autodesk.Civil.*`) are patchier.
Treat missing members as "check the docs / run it," not "it doesn't exist."

### Option B — Generate your own stubs from the DLLs (most accurate)

The DLLs on your machine are the ground truth. Generate stubs directly from them so
they match **your exact Civil 3D 2025 build**:

```bash
pip install pythonnet stubgenlib   # or use `stubgen` from mypy as a fallback
```

The key assemblies (Civil 3D 2025 default paths):

```text
C:\Program Files\Autodesk\AutoCAD 2025\accoremgd.dll
C:\Program Files\Autodesk\AutoCAD 2025\acdbmgd.dll
C:\Program Files\Autodesk\AutoCAD 2025\acmgd.dll
C:\Program Files\Autodesk\AutoCAD 2025\C3D\AeccDbMgd.dll
C:\Program Files\Autodesk\AutoCAD 2025\C3D\AeccPressurePipesMgd.dll
```

🔀 **Path changes by version:** replace `AutoCAD 2025` with `AutoCAD 2024`,
`AutoCAD 2023`, etc. The `C3D` subfolder and DLL names are stable across recent
versions; only the year changes.

!!! tip "Commit the stubs to the repo"
    Put generated stubs in `typings/` and commit them. Every developer then gets
    identical autocomplete without regenerating, and it's version-pinned in Git.

### Tell Cursor where the stubs are

Create/edit `.vscode/settings.json` in the repo root:

```json
{
  "python.analysis.stubPath": "/mnt/c/Users/pbarane1/Documents/c3d2025_stubs_v6/typings",
  "python.analysis.extraPaths": [
    "/mnt/c/Users/pbarane1/Documents/c3d2025_stubs_v6/typings"
  ],
  "python.analysis.typeCheckingMode": "basic",
  "python.analysis.autoImportCompletions": true,
  "python.analysis.diagnosticSeverityOverrides": {
    "reportMissingImports": "none",
    "reportMissingModuleSource": "none"
  },
  "cursorpyright.analysis.autoImportCompletions": true,
  "cursorpyright.analysis.diagnosticSeverityOverrides": {
    "reportMissingImports": "none",
    "reportMissingModuleSource": "none"
  },
  "cursorpyright.analysis.extraPaths": [
    "/mnt/c/Users/pbarane1/Documents/c3d2025_stubs_v6/typings"
  ],
  "cursorpyright.analysis.stubPath": "/mnt/c/Users/pbarane1/Documents/c3d2025_stubs_v6/typings",
  "cursorpyright.analysis.typeCheckingMode": "basic"
}
```

!!! note "Why silence `reportMissingImports`"
    `clr`, `Autodesk.*`, and `System.*` resolve only inside Civil 3D. Locally they'd
    be flagged as missing imports and drown you in red squiggles. Silencing *just*
    those two diagnostics keeps real errors visible while ignoring the unavoidable ones.

---

## Step 4 — Make the Civil 3D imports resolvable for autocomplete

Even with stubs, `clr.AddReference("AeccDbMgd")` then `from Autodesk.Civil... import`
confuses the static analyzer (the reference is added at *runtime*). Two clean ways to
keep autocomplete working:

**Recommended — a guarded typing shim** at the top of each script:

```python
import clr
clr.AddReference("AcCoreMgd")
clr.AddReference("AcDbMgd")
clr.AddReference("AcMgd")
clr.AddReference("AeccDbMgd")

from Autodesk.AutoCAD.ApplicationServices.Core import Application
from Autodesk.AutoCAD.DatabaseServices import Transaction, ObjectId, OpenMode
from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.Civil.DatabaseServices import Alignment, Profile, ProfileView
```

With the stubs in `typings/`, Pylance resolves those `from ... import` lines and
autocomplete lights up on `Alignment.`, `ObjectId.`, etc.

!!! tip "Verify autocomplete works now"
    Type `ObjectId.` in a `.py` in the repo. You should see `Null` and other members
    pop up. If nothing appears: (1) is the folder open as a workspace? (2) is
    `stubPath` correct? (3) reload the window (`Ctrl+Shift+P → Reload Window`).

---

## Step 5 — Linting (catch the gotchas before Dynamo does)

Install **Ruff** — it flags bare `except:`, unreachable-code-after-`return`, unused
vars, and more (exactly the [gotchas](../gotchas.md) list):

```bash
pip install ruff
```

Add `ruff.toml` at the repo root:

```toml
target-version = "py39"
line-length = 100

[lint]
select = ["E", "F", "W", "B", "UP", "SIM"]
# B012/B014 = broad-except issues; F811 unreachable; F841 unused; E722 bare except
ignore = ["E501"]     # long lines OK; we wrap manually
```

!!! success "Ruff catches 4 of our top gotchas statically"
    Bare `except:` (E722), unreachable code after `return` (part of `F`),
    broad-except patterns (`B`), and unused variables (F841) — all flagged in Cursor
    as you type, before you ever open Dynamo.

---

## Step 6 — Load the house rules for Cursor's AI

Drop the `.cursorrules` from the [Cursor Prompt Pack](../_cursor-prompts.md#1-system-prompt-put-in-cursorrules)
at the repo root. Now when you ask Cursor's AI to write or review Civil 3D code, it
follows our lock/transaction, safe-input, error-handling, and crossing-detection
standards instead of generic (often wrong) snippets.

!!! tip "Commit `.cursorrules`, `.vscode/settings.json`, `ruff.toml`, and `typings/`"
    These four make the environment reproducible. A new developer clones the repo,
    opens it in Cursor, selects a Python 3.9 interpreter, and has the full setup —
    autocomplete, linting, and AI standards — in minutes.

---

## Setup verification checklist

- [ ] Repo folder open as a workspace in Cursor.
- [ ] Python 3.9 interpreter selected (matches Dynamo's engine major.minor).
- [ ] `typings/` present; `ObjectId.` triggers autocomplete.
- [ ] `clr` / `Autodesk.*` imports are **not** flagged as errors.
- [ ] Ruff flags a deliberately-added bare `except:` (test it, then remove).
- [ ] `.cursorrules` loaded — ask the AI "what rules are you following?" to confirm.

Next: [Dynamo node workflow](dynamo-node-workflow.md) — importing your `.py` as a
node and the edit→reload→run loop.

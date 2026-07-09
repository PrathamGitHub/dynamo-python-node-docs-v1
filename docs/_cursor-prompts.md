# Cursor / AI Prompt Pack

!!! note "What this page is"
    A collection of ready-to-paste prompts for use with **Cursor**, Copilot Chat, or
    any LLM, when writing or reviewing Civil 3D Dynamo/Python automation. They
    encode the standards from these docs so the AI produces code that matches our
    conventions instead of generic (often wrong) Civil 3D snippets.

    This page is prefixed with `_` so it stays out of the main nav — it's a
    developer aid, not end-user documentation.

---

## How to use

1. Open Cursor in your automation repo.
2. Paste the **System / rules prompt** (below) into `.cursorrules` at the repo root
   (or into the chat's system field).
3. Use the **task prompts** as needed while coding.

---

## 1. System prompt (put in `.cursorrules`)

```text
You are helping write Autodesk Civil 3D automation in Python, run inside Dynamo
Python nodes (CPython 3) via the .NET API (clr).

Always follow these house rules:

STRUCTURE
- Wrap all drawing-modifying work in: doc.LockDocument() → StartTransaction() →
  Commit() → dispose transaction (finally) → dispose lock (finally).
- Commit only on success. Dispose both the transaction and the lock in finally.
- Register every code-created DB object with tr.AddNewlyCreatedDBObject(obj, True).
- Output a single `results` dict with keys: Warnings, Skipped, and any counts.
  End the node with OUT = results.

INPUTS
- Never read IN[i] directly. Use safe readers (_opt_str/_opt_int/_opt_float) that
  return a default on missing/None/wrong-type. Normalise list inputs.

ERRORS
- Never use a bare `except:`. Catch `Exception as e` (or narrower) and append a
  specific message including the failing item and step to results["Warnings"].
- Raise ONLY for fatal setup problems (missing target network; a surface the user
  explicitly requested but that doesn't exist). Otherwise degrade + record.
- Narrow try/except to individual fallible calls, not whole loop bodies.

CIVIL 3D SPECIFICS
- ObjectId is a handle; fetch objects with tr.GetObject inside the transaction.
- For .NET methods with `out double` params (StationOffset, PointLocation), pass
  dummy 0.0 doubles and unpack the return tuple (e.g.
  `_, st, off = aln.StationOffset(x, y, st, off)`). No clr.Reference on CPython 3.
- Resolve styles by name with a find-or-first fallback that warns on miss and
  raises only on an empty collection.
- For label styles, try a priority-ordered LIST of candidate collection paths;
  never hard-code one path. Pressure styles live on the pressure extension.
- For Get...Items() band editing, always pair with the matching Set...Items().
- Handle duplicate-name errors by retrying with an integer suffix.
- Use hasattr() capability checks instead of exact type checks for version
  resilience. Guard optional modules with HAS_* feature flags.

GEOMETRY
- Prefer pure-Python 2-D math over CAD-API intersection calls.
- "Is it a crossing?" needs THREE conditions: segments intersect (0≤t,u≤1) AND
  crossing angle above a threshold AND the hit is not at the candidate's endpoint.
  Never classify a crossing from intersection alone. Note that 2-D tests ignore Z.
- Expose thresholds as inputs and tune them against logged diagnostics.

STYLE
- Comment the "why", not the "what". Prefer clear names over cleverness.
- Make failures visible: a good script reports exactly what failed on which item.
```

---

## 2. Task prompt — write a new automation

```text
Write a Civil 3D Dynamo Python-node script (CPython 3, .NET API via clr) that:
<describe the goal>.

Follow my .cursorrules. Specifically:
- Use the lock → transaction → commit skeleton with finally-disposal.
- Read all inputs with safe _opt_* readers; list the IN[] index for each.
- Resolve every style with find-or-first + warnings.
- Output a results dict (Warnings, Skipped, counts) and end with OUT = results.
Explain any Civil 3D-specific gotchas you handled.
```

---

## 3. Task prompt — review / harden existing code

```text
Review this Civil 3D Dynamo Python script against my .cursorrules. For each issue,
give: (a) the line, (b) which rule it violates, (c) the corrected code, (d) a one-
line 'why the original was weaker'. Prioritise: bare excepts, missing Commit/lock/
AddNewlyCreatedDBObject, out-param handling, single-condition crossing tests,
hard-coded label-style paths, and Get...without Set... Paste the script below.
```

---

## 4. Task prompt — debug "nothing happens, no error"

```text
This Civil 3D Dynamo Python node runs without error but produces no output/changes.
Diagnose likely causes in priority order, based on my .cursorrules:
1. A bare except: swallowing a real error.
2. Missing tr.Commit().
3. Missing doc.LockDocument() (eLockViolation).
4. An out-param method (StationOffset/PointLocation) called without dummy out doubles.
5. Get...Items() modified but never Set...Items() back.
6. Unreachable code after a return.
For each, tell me how to confirm it and how to fix it. Script below.
```

---

## 5. Task prompt — explain a snippet to a newcomer

```text
Explain this Civil 3D Python snippet to a Python developer who is NEW to the Civil
3D .NET API. Use plain-language analogies (ObjectId = coat-check ticket, transaction
= safe workbench, style = outfit). Call out any version-specific or gotcha behaviour.
Keep it under 200 words. Snippet below.
```

---

!!! tip "Keep the rules in the repo"
    Commit `.cursorrules` alongside the code so every developer — and every AI
    assistant — writes to the same standard. Update it when this documentation
    evolves; the two should never drift apart.

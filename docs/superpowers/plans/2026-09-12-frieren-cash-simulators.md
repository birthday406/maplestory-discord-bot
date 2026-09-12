# Frieren Cash Simulators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add accurate Frieren Signature and Heroic Wonderberry Discord simulators with GMS client result artwork.

**Architecture:** Extend the established PSSB command pattern in `maple_bot.py`, using separate rate parsers, costs, renderers and views for each product. Add a focused PowerShell extractor that reads the installed GMS WZ folders and produces only the committed UI and reward icon assets needed by these rotations.

**Tech Stack:** Python 3.10+, discord.py, aiohttp, Pillow, unittest, PowerShell, WzComparerR2.WzLib

**Spec:** `docs/superpowers/specs/2026-09-12-frieren-cash-simulators-design.md`

## Global Constraints

- Both commands offer exactly 1 or 5 draws.
- Signature purchase pricing is 1 for 7,900 NX and 10 for 79,000 NX.
- Wonderberry purchase pricing is 1 for 4,000 NX and 11 for 40,000 NX.
- Runtime rates come from the official Nexon API and empty or malformed tables fail closed.
- Existing `/스스비` and uncommitted ranking work remain unchanged.
- User-facing changes are documented in Korean README and CHANGELOG.

---

### Task 1: Rate tables and product costs

**Files:**
- Modify: `maple_bot.py`
- Test: `test_maple_bot.py`

**Interfaces:**
- Produces: `parse_signature_rates(source)`, `parse_wonderberry_rates(source)`, `signature_nx_cost(count)`, `wonderberry_nx_cost(count)`

- [ ] **Step 1: Write failing parser and cost tests** for two-column Signature rows, three-column Wonderberry rows, total rows, 1/5 choices and bundle boundary costs.
- [ ] **Step 2: Run the focused tests** with `python -m unittest test_maple_bot.RateParsingTests test_maple_bot.SignatureCommandTests test_maple_bot.WonderberryCommandTests -v` and confirm the new names fail.
- [ ] **Step 3: Implement minimal parsers and bundle cost helpers** without changing `parse_pssb_rates` or `pssb_nx_cost`.
- [ ] **Step 4: Run the focused tests** and confirm all Task 1 cases pass.

### Task 2: Client assets and reward icons

**Files:**
- Create: `tools/export_frieren_simulator_assets.ps1`
- Create: `assets/signature-slot-common.png`
- Create: `assets/signature-slot-special.png`
- Create: `assets/wonderberry-backeffect.png`
- Create: `assets/wonderberry-slot-common.png`
- Create: `assets/wonderberry-slot-special.png`
- Create: `data/frieren-simulator-icons.zip`

**Interfaces:**
- Produces: deterministic PNG files and an icon ZIP keyed by exact official reward names.

- [ ] **Step 1: Add the targeted extractor** using read-only GMS `UI`, `String`, `Item` and `Character` WZ inputs and a temporary output directory.
- [ ] **Step 2: Extract UI layers and current reward icons** from the installed v.271 client, resolving `_outlink` aliases such as the Heroic Frieren pet IDs.
- [ ] **Step 3: Validate generated assets** by opening every PNG with Pillow and checking every ZIP entry can be decoded.

### Task 3: Result rendering and Discord views

**Files:**
- Modify: `maple_bot.py`
- Test: `test_maple_bot.py`

**Interfaces:**
- Consumes: Task 1 rates/costs and Task 2 assets.
- Produces: result draw, format, image, embed, file and view functions for both products.

- [ ] **Step 1: Write failing image, embed and reroll tests** asserting one message, one attachment, 1/5 image sizes, cumulative cost, owner checks and fresh rate fetching.
- [ ] **Step 2: Run the focused tests** and confirm the renderer and view names fail.
- [ ] **Step 3: Implement Signature rendering** with representative Frieren item icons and the existing PSSB composition pattern.
- [ ] **Step 4: Implement Wonderberry rendering** with extracted client background and common/special slot treatment.
- [ ] **Step 5: Implement the two views** with 24-hour timeout, reroll and ephemeral expectation output.
- [ ] **Step 6: Run focused tests** and confirm Task 3 passes.

### Task 4: Commands, fetchers and documentation

**Files:**
- Modify: `maple_bot.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: `test_maple_bot.py`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: globally synced `/signature`→`/시그니처` and `/wonderberry`→`/원더베리` commands.

- [ ] **Step 1: Write failing command tests** for localization, 1/5 choices, API fetch error messages and global command registration.
- [ ] **Step 2: Implement fetch methods and commands** using deferred responses and one composite result image.
- [ ] **Step 3: Add both commands to help and setup synchronization**, then update Korean README and the 2026-09-12 CHANGELOG entry.
- [ ] **Step 4: Run focused command tests** and confirm they pass.
- [ ] **Step 5: Run `python -m unittest -v`** and fix only regressions caused by this feature.
- [ ] **Step 6: Review `git diff --check` and `git diff --stat`**, confirming the pre-existing ranking changes remain intact.

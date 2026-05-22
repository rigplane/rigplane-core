---
robots: noindex, follow
---

# GitHub Project Workflow

RigPlane Core uses GitHub Issues plus GitHub Projects as the lightweight
control plane for planning, agent handoff, and implementation tracking.

The goal is to reduce context loss during agent-assisted development while
keeping public open-core issues and PRs easy to understand.

## Project

- Project: `RigPlane Core Roadmap`
- URL: https://github.com/orgs/rigplane/projects/2
- Owner: `rigplane`
- Number: `2`
- Repo: `rigplane/rigplane-core`

The project is linked to the repository. Agents should use it for non-trivial
work items, especially epics, features, spikes, release work, protocol/API
changes, review fixes, and cross-cutting maintenance.

## Source of Truth

Issues are the source of truth for:

- problem statement;
- acceptance criteria;
- API, CLI, protocol, config, and compatibility expectations;
- constraints and non-goals;
- test plan;
- links to specs, plans, PRs, and follow-up issues.

Project fields are only routing metadata:

- current state;
- type of work;
- subsystem area;
- priority;
- phase;
- owner class;
- rough size and risk.

Do not hide requirements in Project fields. Put requirements in the issue body.

## Field Taxonomy

Use these project fields consistently:

| Field | Values | Meaning |
| --- | --- | --- |
| `Status` | `Todo`, `In Progress`, `Done` | Execution state. Keep this current. |
| `WorkType` | `epic`, `feature`, `spike`, `bug`, `debt`, `docs`, `release` | Shape of work. |
| `Area` | `api`, `protocol`, `transport`, `rigctld`, `audio`, `radio-models`, `web-ui`, `cli`, `docs`, `ci`, `release`, `architecture` | Main subsystem or product area. |
| `Priority` | `P0`, `P1`, `P2`, `P3` | Delivery priority. |
| `Phase` | `inbox`, `spec`, `alpha`, `beta`, `stable`, `post-release`, `backlog` | Product/release phase or planning bucket. |
| `Owner` | `human`, `codex`, `mixed` | Who is expected to drive the next step. |
| `Risk` | `low`, `medium`, `high` | API, protocol, release, hardware, or support risk. |
| `Size` | `S`, `M`, `L` | Rough implementation size. |

Labels still matter. Use repo labels for broad search and GitHub-native
filtering, such as `area:audio`, `area:api`, `area:web-ui`, `type:bug`,
`type:feature`, `priority:P1`, `epic`, and `testing`.
Use Project fields for board and roadmap views.

## Project Views

GitHub exposes Project views through GraphQL for reading, but the currently
available public mutations do not include creating or editing Project views.
Configure saved views in the GitHub UI and keep this document as the canonical
view spec.

Recommended views:

| View | Layout | Filter | Group / Sort | Purpose |
| --- | --- | --- | --- | --- |
| `Roadmap` | Table | `WorkType:epic` | Group by `Phase`, sort by `Priority` | Public epic overview. |
| `Agent Queue` | Table | `Status:Todo Owner:codex, mixed -WorkType:epic` | Group by `Area` | Issues that agents can pick up. |
| `Current Work` | Board | `Status:Todo, In Progress` | Group by `Status` | Small execution board. |
| `Review / Closing` | Table | `Status:In Progress` | Show linked PRs and sub-issue progress | Work that needs review, merge, or closure. |
| `Blocked / Risk` | Table | `Risk:high` | Group by `Area` | High-risk items needing human attention. |
| `API / Protocol` | Table | `Area:api, protocol, transport, rigctld` | Group by `Status` | Compatibility-sensitive work. |
| `Web / UX` | Table | `Area:web-ui, cli, docs` | Group by `Status` | Public user-facing surfaces. |

## Creating Views in GitHub UI

The CLI/API can create projects and fields, but saved views must currently be
created by hand.

Use this path:

1. Open https://github.com/orgs/rigplane/projects/2.
2. Click `+ New view` in the view tab row.
3. Choose the layout:
   - `Table` for roadmap, queues, focused lists;
   - `Board` for `Current Work`.
4. Paste the filter text from the table above into the filter bar.
5. Open the view menu (`...`) and set grouping:
   - `Group by` → choose the listed field;
   - `Sort by` → choose `Priority` where listed.
6. Use the view tab menu to rename the view.
7. Repeat for the recommended views.

GitHub persists view configuration server-side once saved in the UI.

## Agent Intake Rules

Before starting non-trivial work, an agent must:

1. Check whether an issue already exists.
2. If no issue exists, create one with acceptance criteria.
3. Add the issue to `RigPlane Core Roadmap`.
4. Set Project fields enough for routing.
5. Work from the issue, not from memory.

Small direct fixes can skip project ceremony only when the change is obvious,
local, and low-risk. If a PR would need a paragraph of explanation, create or
use an issue.

## Recommended Status Flow

Use this simple state machine:

```text
Inbox idea -> issue created -> Project Status=Todo
Spec/design work starts -> Status=In Progress, Phase=spec
Implementation starts -> Status=In Progress
PR ready/merged -> Status=Done when acceptance criteria are satisfied
```

GitHub Projects currently has the default `Status` options `Todo`,
`In Progress`, and `Done`. More granular states such as `Blocked` or `Review`
can be expressed with labels or issue comments until the Project views need
that extra structure.

## CLI Setup

The local GitHub token needs `project` scope:

```bash
gh auth status
gh auth refresh -s project
```

List projects:

```bash
gh project list --owner rigplane
```

View this project:

```bash
gh project view 2 --owner rigplane
gh project field-list 2 --owner rigplane
gh project item-list 2 --owner rigplane
```

Add an issue:

```bash
gh project item-add 2 \
  --owner rigplane \
  --url https://github.com/rigplane/rigplane-core/issues/1430
```

The command returns a Project item ID. Use that ID with `gh project item-edit`
to set field values.

## Editing Fields From CLI

`gh project item-edit` requires:

- project ID;
- item ID;
- field ID;
- option ID for single-select fields.

Fetch field IDs and option IDs:

```bash
gh project field-list 2 --owner rigplane --format json
```

Then edit one field per invocation:

```bash
gh project item-edit \
  --project-id PVT_kwDOEMw05M4BWz12 \
  --id <PROJECT_ITEM_ID> \
  --field-id <FIELD_ID> \
  --single-select-option-id <OPTION_ID>
```

GitHub's Project API can conflict when creating fields in parallel. Create or
modify Project fields serially.

## Issue Creation Standards

Every implementation issue should contain:

- concise summary;
- acceptance criteria as checkboxes;
- implementation notes or constraints;
- compatibility impact:
  - public API;
  - CLI;
  - config files;
  - rigctld wire behavior;
  - docs;
- test plan.

Epics should additionally contain:

- product or architecture intent;
- scope and non-goals;
- risks;
- suggested child issues;
- completion criteria.

Spikes should contain:

- question to answer;
- time-box;
- exact deliverable;
- decision criteria.

## PR Rules

Every non-trivial PR should link an issue.

Before opening or finalizing a PR:

1. Confirm the linked issue is in the Project.
2. Confirm the issue acceptance criteria still match the implementation.
3. Update Project `Status` to `In Progress` while implementation is active.
4. When merged and accepted, update `Status` to `Done`.
5. Close the issue with a comment that maps delivered work to acceptance
   criteria, unless the PR uses an explicit `Closes #N` / `Fixes #N` keyword.

If the implementation discovers new scope, create follow-up issues instead of
silently expanding the original issue.

## Agent Queue

Agents should prefer issues with:

- `Status=Todo`;
- clear acceptance criteria;
- `Owner=codex` or `Owner=mixed`;
- known `Area`;
- `Risk` and `Size` filled in for medium/large work.

Agents should avoid starting:

- `epic` items directly unless asked to decompose or implement the epic;
- issues without acceptance criteria;
- issues whose compatibility impact is unknown;
- `P0` work without confirming urgency and blast radius.

## Bootstrap Items

The project was bootstrapped with the currently open issues:

- `#1430`: `WorkType=bug`, `Area=audio`, `Priority=P1`, `Phase=inbox`,
  `Owner=codex`, `Risk=medium`, `Size=S`.
- `#727`: `WorkType=feature`, `Area=radio-models`, `Priority=P3`,
  `Phase=backlog`, `Owner=mixed`, `Risk=medium`, `Size=S`.

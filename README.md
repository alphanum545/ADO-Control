# ADO Control Center

Standalone web console for LLM-assisted Azure DevOps operations.

The app lets you enter an objective such as:

- Create a new project
- Create a sprint
- Create work items in a sprint
- Read, update, or delete work items
- Update or delete sprints
- Update or delete projects

Every objective is handled in two stages:

1. The LLM creates a JSON action plan.
2. The app validates the plan and shows a dry run.
3. You type `APPLY` and click Apply to execute the validated plan.

Deletes are blocked unless you enable the delete gates in the sidebar. Project deletion has its own extra gate.

## Setup

```bash
cd "C:/Users/SilaparasettiLohithM/OneDrive - MAQ Software/Desktop/Agentic AI/AI ML/project-pulse-oss/ado-control-center"
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

```env
ADO_ORG=your-ado-org
ADO_PAT=your-personal-access-token
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
DEFAULT_WORK_ITEM_ASSIGNEE=silaparasetti.lohith@maqsoftware.com
```

Your Azure DevOps PAT needs the scopes for the operations you enable:

- Work item read/write for work item CRUD
- Project and team read/write/manage for project CRUD
- Work item metadata permissions for iteration/sprint CRUD

## Run Locally

```bash
streamlit run app.py
```

Open the URL Streamlit prints, usually:

```text
http://localhost:8501
```

## Run From GitHub Actions

This repo includes a manual workflow at `.github/workflows/ado-agent.yml`.
Use it as a hosted control panel when you do not want to expose the Streamlit app.

In GitHub, add these repository secrets:

```text
ADO_ORG
ADO_PAT
LLM_API_KEY
```

Optional repository secrets or variables:

```text
LLM_BASE_URL
LLM_MODEL
LLM_TEMPERATURE
DEFAULT_WORK_ITEM_ASSIGNEE
DEFAULT_PROJECT_PROCESS
DEFAULT_PROJECT_VISIBILITY
```

For the apply gate, create a GitHub environment named `ado-apply` and add
required reviewers. The workflow has two jobs:

1. `plan`: always runs first and only dry-runs the validated ADO plan.
2. `apply`: runs only when `apply=true`, uses the `ado-apply` environment gate,
   downloads the exact plan artifact from the plan job, and applies that plan.

To use it:

1. Open the repo in GitHub.
2. Go to **Actions**.
3. Select **ADO Agent**.
4. Click **Run workflow**.
5. Enter the objective, project/context options, and keep `apply=false` first.
6. Review the run summary or `ado-agent-plan` artifact.
7. Re-run with `apply=true` only when the plan is correct.

## Docker

```bash
docker compose up --build
```

## Example Objectives

Create a project:

```text
Create a private project named AI Ops Sandbox using the Agile process.
```

Create a sprint:

```text
Create a sprint named AI Test Sprint from 2026-05-01 to 2026-05-15 in project Alpha.
```

Create sprint plus work items:

```text
Create a sprint named AI Test Sprint in Alpha, then create two Task work items in that sprint for the stale work item context.
```

Update a work item:

```text
Update work item 163 in Alpha by adding the tag AI-Test.
```

Delete a throwaway work item:

```text
Delete work item 999 in Alpha because it is a throwaway AI test item.
```

Enable "Allow deletes in plan" before generating the plan, then type `APPLY` before execution.

## Safety Model

- The LLM never writes directly to ADO.
- The app validates resource, operation, fields, and delete gates.
- Dry run is always shown before execution.
- Newly created work items are always assigned to `silaparasetti.lohith@maqsoftware.com`.
- Project creation is asynchronous in Azure DevOps, so the app does not create sprints or work items inside a newly created project in the same plan.

## API Notes

This app uses Azure DevOps REST API 7.1:

- Work Items API for create/update/delete.
- Classification Nodes API for sprint/iteration CRUD.
- Core Projects API for project create/update/delete.
- Core Processes API to resolve a process template for project creation.

# Documentation

This directory is the canonical location for project documentation.

## Structure

- `guide/`
  - Documents for internal engineers and operators.
  - Use this for onboarding, development guides, operation workflows, and troubleshooting guides.

- `manual/`
  - Documents for customers and external users.
  - Use this for installation manuals, usage manuals, release-facing instructions, and customer-visible procedures.
  - Current manuals: [JIBOT Adapter SSH Update Manual](manual/jibot-adapter-ssh-update.md),
    [SSH 키 생성 및 관리 매뉴얼](manual/ssh-key-management.md).

- `reference/`
  - Documents for AI agents and implementation lookup.
  - Use this for schemas, protocol mappings, field definitions, API references, code structure notes, and machine-readable context.

- `todo/`
  - Documents for pending work.
  - Use this for issue lists, investigation notes, implementation plans, and prioritized technical debt.

## Writing Rule

Choose the directory by audience first:

1. Human project members: `guide/`
2. Customers or external users: `manual/`
3. AI agents or implementation lookup: `reference/`
4. Pending work or known gaps: `todo/`

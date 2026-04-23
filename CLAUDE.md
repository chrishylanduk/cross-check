# Claude Instructions for Cross-check

## Project Overview

Cross-check is an AI-assisted content audit tool that automatically recommends improvements to large collections of written content (such as websites or intranets). It helps improve consistency, clarity, compliance, and completeness whilst saving hours or days compared to manual content audits.

## Language

**Always use British English spelling and grammar conventions** when writing code comments, documentation, user-facing text, or any content for this project.

Examples:
- "analyse" not "analyze"
- "colour" not "color"
- "organise" not "organize"
- "behaviour" not "behavior"

## Technology Stack

- **Frontend**: Next.js with GOV.UK Frontend design system (customised with orange branding)
- **Backend**: Python (FastAPI)
- **AI**: Integration with AI agents for content analysis

## Code Style

- Follow existing patterns in the codebase
- Keep components simple and focused
- Use British English in all user-facing text and code comments

## Design System

The project uses GOV.UK Frontend components but with custom orange branding instead of the standard blue. The service is not an official government service, so:
- Crown logos are hidden
- Custom fonts are used (system fonts instead of Transport)
- Footer links should only be added when the corresponding pages exist

## Agent Configuration

See [agents.md](./agents.md) for detailed information about the AI agents used in this project.

## Development Workflow

- Pages that don't exist yet should not be linked from the UI
- Keep the interface clean and minimal until features are implemented
- Focus on core content audit functionality

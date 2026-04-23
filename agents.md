# Agents

This document describes the AI agents and their configuration for the Cross-check service.

## Overview

Cross-check uses AI agents to automatically analyse and recommend improvements to large collections of written content. The system evaluates content for:

- **Consistency** - Ensuring terminology, style, and tone are uniform across content
- **Clarity** - Identifying unclear or confusing language
- **Compliance** - Checking adherence to style guides and regulations
- **Completeness** - Detecting missing information or broken references

## Agent Configuration

### Content Analysis Agent

The primary agent analyses uploaded content and generates recommendations based on:

1. Content style guidelines
2. Readability metrics
3. Terminology consistency
4. Structural completeness

### Future Agents

Additional agents may be developed for:

- Accessibility checking
- Plain English assessment
- Cross-reference validation
- Duplicate content detection

## Development Notes

- All content analysis should respect British English spelling and grammar conventions
- Agents should provide actionable, specific recommendations rather than generic feedback
- Processing should handle various content formats (HTML, Markdown, plain text)

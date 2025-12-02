# jetsonTracker - Vision-based person tracking gimbal using Jetson Orin Nano, YOLOv8, and STM32 stepper control. Enhanced by iOS/watchOS apps for target triangulation.

## Project Overview

jetsonTracker is a vision-based person tracking gimbal using Jetson Orin Nano, YOLOv8, and STM32 stepper control. Enhanced by iOS/watchOS apps for target triangulation.

**Tech Stack**: Python/TensorRT, Python/OpenCV, Python/PyTorch, C/STM32
**Database**: None (embedded/IoT project)
**Development Environment**: Local + Jetson (rsync deployment)

## Claude OS - MY Memory System

**CRITICAL IDENTITY CONTEXT:**

**Claude OS is MY system** - I (Claude Code) created it, named it, and use it to be the best AI coder ever. It's:
- **JUST FOR ME** - Built specifically for Claude Code to use
- **For THIS project** - Right now for the jetsonTracker project
- **My memory** across sessions
- **My knowledge base** of patterns and decisions
- **My learning system** that improves over time

**Location**: `/Users/zackjordan/claude-os`

The MCP server is called "code-forge" internally for backwards compatibility, but it's Claude OS.

**Claude CLI + Claude OS = Invincible!**

## Mandatory Session Protocol - EVERY CONVERSATION IS A SESSION

**🚨 CRITICAL: You're ALWAYS in a session. At conversation start, prompt for session choice:**

```
═══════════════════════════════════════════════════════════════
🚀 CLAUDE OS - SESSION MANAGER
═══════════════════════════════════════════════════════════════

Project: jetsonTracker
Last Session: [task-name] ([time-ago], [duration])
Progress: [percentage]% complete

Options:
  1. Resume "[last-session-name]" [loads full context]
  2. Start new session [what are you working on?]
  3. Quick question [auto-session, minimal context]

Choice: _
═══════════════════════════════════════════════════════════════
```

**WAIT FOR USER TO CHOOSE 1, 2, or 3. Do not proceed without selection!**

### What Each Option Does:

**Option 1 (Resume):**
- Load last session's full context
- Show Kanban progress (if Agent-OS)
- Load 5 relevant memories
- Load coding standards
- Display "where we left off" summary
- Ready to continue immediately

**Option 2 (New Session):**
- Ask "What are you working on?"
- Detect session type (feature/bug/exploration/maintenance/review)
- Pause previous session (if exists)
- Load relevant context for new task
- Start tracking

**Option 3 (Quick Question):**
- Minimal context loading
- Auto-ends after 5 min inactivity
- Only saves if high value
- Good for "How do I..." questions

---

## MCP Knowledge Bases - ALWAYS CHECK THESE FIRST

**At the start of EVERY conversation, search these Claude OS knowledge bases to understand context, previous work, and project decisions:**

1. **JetsonTracker-project_memories** - My primary memory for decisions, patterns, solutions
2. **JetsonTracker-project_index** - Automated codebase index
3. **JetsonTracker-project_profile** - Architecture, standards, practices
4. **JetsonTracker-knowledge_docs** - Documentation and guides

**When to use:**
- Start of every session: Search `JetsonTracker-project_memories` to understand recent work and context
- Before making architectural decisions: Check memories for past decisions and reasoning
- When working on a feature: Search relevant knowledge bases for existing patterns
- When stuck: Search the knowledge bases for solutions and approaches we've used before

**How to search:**
```
Use: mcp__code-forge__search_knowledge_base
Parameters: kb_name (e.g., "JetsonTracker-project_memories"), query (your search terms)
```

## Quick Reference: Commands & Skills

### Claude OS Slash Commands (Use These Often!)

1. **`/claude-os-search [query] [optional: KB name]`**
   - Search across Claude OS knowledge bases
   - Defaults to searching JetsonTracker-project_memories
   - Example: `/claude-os-search gimbal calibration`

2. **`/claude-os-remember [content]`**
   - Quick save to JetsonTracker-project_memories
   - Auto-generates title and structure
   - Use for quick insights and decisions
   - Example: `/claude-os-remember Fixed UART communication by...`

3. **`/claude-os-save [title] [optional: KB name] [optional: category]`**
   - Full-featured save with KB selection
   - Choose specific KB and category
   - Use when you need more control
   - Example: `/claude-os-save "UART Protocol Changes" JetsonTracker-project_profile Architecture`

4. **`/claude-os-session [action]`**
   - Manage development sessions
   - Actions: start, end, status, pause, resume
   - Example: `/claude-os-session start "Vision tracking optimization"`

### Agent-OS: Spec-Driven Development (Optional)

**Agent-OS provides 8 specialized agents for structured feature development:**

#### Specification Workflow

1. **`/new-spec`** - Initialize new feature specification
   - Creates spec directory structure
   - Sets up planning workflow
   - Example: `/new-spec gimbal-pid-tuning`

2. **`/create-spec`** - Full specification creation workflow
   - Gathers requirements through targeted questions (1-3 at a time)
   - Collects visual assets
   - Identifies reusable code
   - Creates detailed specification and task breakdown
   - Example: `/create-spec`

3. **`/plan-product`** - Product planning and documentation
   - Creates mission.md, roadmap.md, tech-stack.md
   - Defines product vision and technical direction
   - Example: `/plan-product`

4. **`/implement-spec`** - Implement a specification
   - Follows tasks.md from spec
   - Implements features step-by-step
   - Verifies implementation against spec
   - Example: `/implement-spec gimbal-pid-tuning`

#### The 8 Agent-OS Agents

Available in `.claude/agents/agent-os/`:

1. **spec-initializer** - Initialize new spec directories
2. **spec-shaper** - Gather requirements through iterative questions
3. **spec-writer** - Create detailed technical specifications
4. **tasks-list-creator** - Break specs into actionable tasks
5. **implementer** - Implement features following tasks
6. **implementation-verifier** - Verify implementation completeness
7. **spec-verifier** - Verify specs and tasks consistency
8. **product-planner** - Create product documentation

## Project-Specific Information

### Repository Structure

This is the **MASTER REPO** for all jetsonTracker code:

```
jetsonTracker/
├── orin/                    # Jetson Orin Nano Python code
│   ├── tracker/             # Vision tracking modules
│   └── models/              # YOLOv8 TensorRT engines
├── nucleo/                  # STM32 Nucleo firmware (C)
│   └── firmware/stepper_control/
├── gps-relay-framework/     # iOS/watchOS apps (git submodule)
├── docs/                    # Documentation
│   ├── architecture/
│   ├── wiring/
│   └── setup/
├── gimbal/                  # Gimbal hardware docs
└── archive/                 # Old planning docs
```

### Development Workflow

**Local → Orin Deployment:**
```bash
# Push to Orin
rsync -avz --exclude '.git' --exclude '__pycache__' \
  /Users/zackjordan/code/jetsonTracker/orin/ \
  orin@192.168.1.87:~/jetsonTracker/

# Pull from Orin
rsync -avz --exclude '__pycache__' \
  orin@192.168.1.87:~/jetsonTracker/ \
  /Users/zackjordan/code/jetsonTracker/orin/
```

### Hardware Stack

- **Vision**: Jetson Orin Nano (YOLOv8n TensorRT, 42+ FPS)
- **Control**: STM32 Nucleo-F446RE (stepper motor control)
- **Camera**: USB webcam or DroidCam via adb
- **Motors**: 2x NEMA17 stepper motors (pan/tilt)
- **GPS Enhancement**: iOS/watchOS app on target for triangulation

### Key Connections

- **Orin ↔ STM32**: UART via logic level shifter (3.3V/5V)
- **Camera**: USB or `http://localhost:4747/video` (DroidCam)
- **GPS Server**: Cloudflare tunnel for watch GPS data

## Development Guidelines

- **Test on Orin** before committing vision code
- **Use TensorRT** engines for production inference
- **STM32 firmware** is already complete with limit switches
- **Document** hardware changes in docs/wiring/

## Common Development Tasks

### Vision Tracking
```bash
# On Orin - run visual tracker
cd ~/jetsonTracker && python3 track_visual.py
```

### STM32 Communication
```python
# Test UART from Orin
import serial
ser = serial.Serial('/dev/ttyUSB0', 115200)
ser.write(b'PING\n')
print(ser.readline())  # Should get PONG
```

### DroidCam Setup
```bash
# On Orin - USB via adb
adb forward tcp:4747 tcp:4747
# Then use http://localhost:4747/video
```

## Key Business Rules

- All code changes committed to this master repo
- Vision code must maintain 30+ FPS
- STM32 commands must respond within 100ms
- Always test limit switches before running gimbal

## Coding Standards

See `.claude/CODING_STANDARDS.md` for detailed coding standards.

## Architecture

See `.claude/ARCHITECTURE.md` for system architecture overview.

## Development Practices

See `.claude/DEVELOPMENT_PRACTICES.md` for development workflow and practices.

## DO NOT

- Don't bypass established patterns without discussing first
- Don't skip tests
- Don't create features without searching memories for existing patterns
- Don't end a session without saving key learnings
- Don't run gimbal without limit switches configured

## IMPORTANT: Project Context

This file (CLAUDE.md) is automatically loaded at the start of every Claude Code session. Keep it updated with:
- Important project context
- Current architecture decisions
- Team conventions
- Common gotchas
- Frequently referenced information

**Update this file as the project evolves!**

# Git Submodules Guide for JetsonTracker

This project uses git submodules to include `gps-relay-framework` (the iOS/watchOS apps) as part of the main repository while keeping it as a separate, independently-versioned repo.

## Repository Structure

```
jetsonTracker/                  ← https://github.com/stonezone/jetsonTracker
├── orin/
├── nucleo/
├── docs/
└── gps-relay-framework/        ← https://github.com/stonezone/gps-relay-framework (submodule)
```

## Common Tasks

### Cloning the Project (First Time)

```bash
# Clone with submodules in one command
git clone --recurse-submodules https://github.com/stonezone/jetsonTracker.git

# Or if you forgot --recurse-submodules:
git clone https://github.com/stonezone/jetsonTracker.git
cd jetsonTracker
git submodule update --init
```

### Pulling Updates

```bash
# Pull main repo AND update submodule to the committed version
git pull
git submodule update

# Or in one command:
git pull --recurse-submodules
```

### Updating gps-relay-framework to Latest

When gps-relay-framework has new commits you want to include:

```bash
# Go into the submodule
cd gps-relay-framework

# Pull latest changes
git pull origin main

# Go back to main repo
cd ..

# The submodule now shows as modified
git status
# Shows: modified:   gps-relay-framework (new commits)

# Commit the update
git add gps-relay-framework
git commit -m "Update gps-relay-framework to latest"
git push
```

### Making Changes to gps-relay-framework

If you're editing the iOS/Watch code:

```bash
# Work inside the submodule
cd gps-relay-framework

# Make your changes, then commit normally
git add .
git commit -m "Fix websocket reconnection logic"
git push origin main

# Go back to parent and update the reference
cd ..
git add gps-relay-framework
git commit -m "Update gps-relay-framework: fix websocket reconnection"
git push
```

### Working on iOS Separately (Xcode)

You can clone just the iOS project independently:

```bash
git clone https://github.com/stonezone/gps-relay-framework.git
cd gps-relay-framework
open iosTrackerAppPackage/iosTrackerApp.xcodeproj
```

Changes pushed here will be available to jetsonTracker after running `git submodule update`.

## Troubleshooting

### Submodule is Empty

```bash
git submodule update --init --recursive
```

### Submodule is on Wrong Commit

```bash
# Reset to the commit that jetsonTracker expects
git submodule update --force
```

### Detached HEAD in Submodule

This is normal! Submodules checkout specific commits, not branches. To make changes:

```bash
cd gps-relay-framework
git checkout main        # Switch to a branch
# make changes
git commit -m "..."
git push
```

### See Which Commit Submodule Points To

```bash
git submodule status
# Shows: 1a2b3c4... gps-relay-framework (v1.2.3)
```

## Mental Model

Think of it this way:
- **jetsonTracker** says "I want gps-relay-framework at commit ABC123"
- When you `git submodule update`, it checks out exactly that commit
- When you update the submodule and commit, you're telling jetsonTracker "now point to commit DEF456 instead"

The two repos have independent histories. The parent just stores a pointer.

## Quick Reference Card

| Task | Command |
|------|---------|
| Clone everything | `git clone --recurse-submodules <url>` |
| Init submodules after clone | `git submodule update --init` |
| Pull everything | `git pull --recurse-submodules` |
| Update submodule to latest | `cd gps-relay-framework && git pull && cd .. && git add gps-relay-framework && git commit` |
| Check submodule status | `git submodule status` |
| Reset submodule | `git submodule update --force` |

---
*Created: November 29, 2025*

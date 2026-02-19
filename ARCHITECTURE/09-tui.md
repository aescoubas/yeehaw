# 09 - TUI Dashboard

Built with Bubble Tea (Elm architecture).

## Layout

```
┌─ YEEHAW ─────────────────────────────────────────────┐
│ Projects              │  Tasks                        │
│ > myapp    3/5 phases │  #12 [RUNNING] Setup   claude │
│   webapp   1/3 phases │  #13 [QUEUED]  Core           │
├───────────────────────┴───────────────────────────────┤
│ Events                                                │
│ 14:30 ✓ task-12 completed │ 14:28 ! verify failed     │
├───────────────────────────────────────────────────────┤
│ [Tab] focus  [w] attach  [y] reply  [s] tick  [q]uit │
└───────────────────────────────────────────────────────┘
```

## Key Bindings

| Key | Action |
|-----|--------|
| Tab | Cycle focus between panels |
| w | Attach to selected task's tmux session |
| y | Send reply/input to selected task |
| s | Force a scheduler tick |
| q | Quit TUI |
| j/k | Navigate up/down in focused panel |

## Components

- **ProjectList** - Left panel, project names with phase progress
- **TaskTable** - Right panel, tasks for selected project
- **EventLog** - Bottom panel, scrolling event feed

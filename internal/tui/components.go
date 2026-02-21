package tui

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/aescoubas/yeehaw/internal/roadmap"
	"github.com/aescoubas/yeehaw/internal/store"
)

// Messages
type projectsMsg struct{ projects []store.Project }
type tasksMsg struct{ tasks []store.Task }
type eventsMsg struct{ events []store.Event }
type tickMsg struct{}
type errMsg struct{ err error }

type editorFinishedMsg struct {
	err      error
	project  store.Project
	filePath string
}

type roadmapImportedMsg struct {
	err       error
	summary   string
	projectID int64
}

type roadmapApprovedMsg struct {
	err       error
	projectID int64
}

// Commands

func loadProjects(db *store.DB) tea.Cmd {
	return func() tea.Msg {
		projects, err := db.ListProjects()
		if err != nil {
			return errMsg{err}
		}
		return projectsMsg{projects}
	}
}

func loadTasksForProject(db *store.DB, projectID int64) tea.Cmd {
	return func() tea.Msg {
		// Get latest roadmap for this project, then get all its tasks
		rm, err := db.GetLatestRoadmap(projectID)
		if err != nil || rm == nil {
			return tasksMsg{nil}
		}

		queued, err := db.GetQueuedTasks(rm.ID)
		if err != nil {
			return errMsg{err}
		}
		running, err := db.GetRunningTasks(rm.ID)
		if err != nil {
			return errMsg{err}
		}

		// Combine: running first, then queued
		var all []store.Task
		all = append(all, running...)
		all = append(all, queued...)
		return tasksMsg{all}
	}
}

func loadEvents(db *store.DB) tea.Cmd {
	return func() tea.Msg {
		events, err := db.RecentEvents(50)
		if err != nil {
			return errMsg{err}
		}
		return eventsMsg{events}
	}
}

func tmuxAttachCmd(sessionName string) *exec.Cmd {
	cmd := exec.Command("tmux", "attach-session", "-t", sessionName)
	// Allow nesting inside an existing tmux session
	for _, e := range os.Environ() {
		if !strings.HasPrefix(e, "TMUX=") {
			cmd.Env = append(cmd.Env, e)
		}
	}
	return cmd
}

func importRoadmap(db *store.DB, project store.Project, filePath string) tea.Cmd {
	return func() tea.Msg {
		data, err := os.ReadFile(filePath)
		if err != nil {
			return roadmapImportedMsg{err: fmt.Errorf("read roadmap: %w", err), projectID: project.ID}
		}

		content := string(data)

		// Skip import if content hasn't changed from the latest DB roadmap
		existing, _ := db.GetLatestRoadmap(project.ID)
		if existing != nil && existing.RawText == content {
			return roadmapImportedMsg{
				summary:   "No changes",
				projectID: project.ID,
			}
		}

		parsed, err := roadmap.Parse(content)
		if err != nil {
			return roadmapImportedMsg{err: fmt.Errorf("parse roadmap: %w", err), projectID: project.ID}
		}

		errs := roadmap.Validate(parsed)
		if len(errs) > 0 {
			return roadmapImportedMsg{
				err:       fmt.Errorf("validation errors:\n  - %s", strings.Join(errs, "\n  - ")),
				projectID: project.ID,
			}
		}

		rmID, err := db.InsertRoadmap(project.ID, content, "draft")
		if err != nil {
			return roadmapImportedMsg{err: fmt.Errorf("insert roadmap: %w", err), projectID: project.ID}
		}

		for _, phase := range parsed.Phases {
			phID, err := db.InsertPhase(rmID, phase.Number, phase.Title, phase.Verification)
			if err != nil {
				return roadmapImportedMsg{err: fmt.Errorf("insert phase: %w", err), projectID: project.ID}
			}
			for _, task := range phase.Tasks {
				if _, err := db.InsertTask(phID, task.Number, task.Title, task.Description); err != nil {
					return roadmapImportedMsg{err: fmt.Errorf("insert task: %w", err), projectID: project.ID}
				}
			}
		}

		taskCount := 0
		for _, p := range parsed.Phases {
			taskCount += len(p.Tasks)
		}

		return roadmapImportedMsg{
			summary:   fmt.Sprintf("Imported %d phases, %d tasks", len(parsed.Phases), taskCount),
			projectID: project.ID,
		}
	}
}

func approveRoadmap(db *store.DB, projectID int64) tea.Cmd {
	return func() tea.Msg {
		rm, err := db.GetLatestRoadmap(projectID)
		if err != nil {
			return roadmapApprovedMsg{err: err, projectID: projectID}
		}
		if rm == nil {
			return roadmapApprovedMsg{err: fmt.Errorf("no roadmap found"), projectID: projectID}
		}
		if rm.Status != "draft" {
			return roadmapApprovedMsg{err: fmt.Errorf("roadmap is %q, not draft", rm.Status), projectID: projectID}
		}
		if err := db.UpdateRoadmapStatus(rm.ID, "approved"); err != nil {
			return roadmapApprovedMsg{err: err, projectID: projectID}
		}
		return roadmapApprovedMsg{projectID: projectID}
	}
}

func tickEvery(d time.Duration) tea.Cmd {
	return tea.Tick(d, func(t time.Time) tea.Msg {
		return tickMsg{}
	})
}

func roadmapTemplate(projectName string) string {
	return fmt.Sprintf(`# Roadmap: %s

## Phase 1: Setup
**Verification:** `+"`make test`"+`

### Task 1.1: First task
Description of what needs to be done.
`, projectName)
}

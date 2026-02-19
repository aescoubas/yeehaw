package tui

import (
	"os/exec"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/aescoubas/yeehaw/internal/store"
)

// Messages
type projectsMsg struct{ projects []store.Project }
type tasksMsg struct{ tasks []store.Task }
type eventsMsg struct{ events []store.Event }
type tickMsg struct{}
type errMsg struct{ err error }

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
	return exec.Command("tmux", "attach-session", "-t", sessionName)
}

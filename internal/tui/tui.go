package tui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/aescoubas/yeehaw/internal/store"
)

// Panel tracks which pane has focus.
type Panel int

const (
	PanelProjects Panel = iota
	PanelTasks
	PanelEvents
)

// Model is the top-level Bubble Tea model.
type Model struct {
	db     *store.DB
	width  int
	height int

	focus    Panel
	projects []store.Project
	tasks    []store.Task
	events   []store.Event

	projectCursor int
	taskCursor    int
	eventScroll   int

	err error
}

// New creates the TUI model.
func New(db *store.DB) Model {
	return Model{
		db:    db,
		focus: PanelProjects,
	}
}

// Init loads initial data.
func (m Model) Init() tea.Cmd {
	return tea.Batch(loadProjects(m.db), loadEvents(m.db))
}

// Update handles messages.
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		return m.handleKey(msg)

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		return m, nil

	case projectsMsg:
		m.projects = msg.projects
		if len(m.projects) > 0 {
			return m, loadTasksForProject(m.db, m.projects[m.projectCursor].ID)
		}
		return m, nil

	case tasksMsg:
		m.tasks = msg.tasks
		return m, nil

	case eventsMsg:
		m.events = msg.events
		return m, nil

	case tickMsg:
		return m, tea.Batch(loadProjects(m.db), loadEvents(m.db))

	case errMsg:
		m.err = msg.err
		return m, nil
	}

	return m, nil
}

func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "q", "ctrl+c":
		return m, tea.Quit

	case "tab":
		m.focus = (m.focus + 1) % 3
		return m, nil

	case "j", "down":
		switch m.focus {
		case PanelProjects:
			if m.projectCursor < len(m.projects)-1 {
				m.projectCursor++
				return m, loadTasksForProject(m.db, m.projects[m.projectCursor].ID)
			}
		case PanelTasks:
			if m.taskCursor < len(m.tasks)-1 {
				m.taskCursor++
			}
		case PanelEvents:
			if m.eventScroll < len(m.events)-1 {
				m.eventScroll++
			}
		}
		return m, nil

	case "k", "up":
		switch m.focus {
		case PanelProjects:
			if m.projectCursor > 0 {
				m.projectCursor--
				return m, loadTasksForProject(m.db, m.projects[m.projectCursor].ID)
			}
		case PanelTasks:
			if m.taskCursor > 0 {
				m.taskCursor--
			}
		case PanelEvents:
			if m.eventScroll > 0 {
				m.eventScroll--
			}
		}
		return m, nil

	case "s":
		return m, tea.Batch(loadProjects(m.db), loadEvents(m.db))

	case "w":
		// Attach to tmux session for selected task
		if m.focus == PanelTasks && m.taskCursor < len(m.tasks) {
			task := m.tasks[m.taskCursor]
			if task.Status == "dispatched" || task.Status == "running" {
				sessName := fmt.Sprintf("yeehaw-task-%d", task.ID)
				return m, tea.ExecProcess(tmuxAttachCmd(sessName), func(err error) tea.Msg {
					return tickMsg{}
				})
			}
		}
		return m, nil
	}

	return m, nil
}

// View renders the TUI.
func (m Model) View() string {
	if m.width == 0 {
		return "Loading..."
	}

	// Styles
	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("229")).
		Background(lipgloss.Color("57")).
		Padding(0, 1)

	activeStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("229"))

	inactiveStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("240"))

	// Calculate dimensions
	leftWidth := m.width/3 - 2
	rightWidth := m.width - leftWidth - 6
	topHeight := m.height - 12 // leave room for events + help
	if topHeight < 5 {
		topHeight = 5
	}

	// Title
	title := titleStyle.Render(" YEEHAW ")

	// Projects panel
	projStyle := inactiveStyle
	if m.focus == PanelProjects {
		projStyle = activeStyle
	}
	projectsView := m.renderProjects(leftWidth, topHeight)
	projectsPanel := projStyle.Width(leftWidth).Height(topHeight).Render(projectsView)

	// Tasks panel
	taskStyle := inactiveStyle
	if m.focus == PanelTasks {
		taskStyle = activeStyle
	}
	tasksView := m.renderTasks(rightWidth, topHeight)
	tasksPanel := taskStyle.Width(rightWidth).Height(topHeight).Render(tasksView)

	// Events panel
	evtStyle := inactiveStyle
	if m.focus == PanelEvents {
		evtStyle = activeStyle
	}
	eventsView := m.renderEvents(m.width-4, 5)
	eventsPanel := evtStyle.Width(m.width - 4).Height(5).Render(eventsView)

	// Help bar
	help := lipgloss.NewStyle().Foreground(lipgloss.Color("241")).Render(
		" [Tab] focus  [w] attach  [s] refresh  [j/k] navigate  [q] quit",
	)

	// Compose
	top := lipgloss.JoinHorizontal(lipgloss.Top, projectsPanel, tasksPanel)
	return lipgloss.JoinVertical(lipgloss.Left, title, top, eventsPanel, help)
}

func (m Model) renderProjects(width, height int) string {
	header := lipgloss.NewStyle().Bold(true).Render("Projects")
	var lines []string
	lines = append(lines, header)

	if len(m.projects) == 0 {
		lines = append(lines, "  (none)")
	}

	for i, p := range m.projects {
		cursor := "  "
		if i == m.projectCursor {
			cursor = "> "
		}
		line := fmt.Sprintf("%s%-*s", cursor, width-2, p.Name)
		if i == m.projectCursor {
			line = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("229")).Render(line)
		}
		lines = append(lines, line)
	}

	return strings.Join(lines, "\n")
}

func (m Model) renderTasks(width, height int) string {
	header := lipgloss.NewStyle().Bold(true).Render("Tasks")
	var lines []string
	lines = append(lines, header)

	if len(m.tasks) == 0 {
		lines = append(lines, "  (no tasks)")
	}

	for i, t := range m.tasks {
		cursor := "  "
		if i == m.taskCursor && m.focus == PanelTasks {
			cursor = "> "
		}
		status := statusBadge(t.Status)
		agentStr := ""
		if t.Agent != "" {
			agentStr = " " + t.Agent
		}
		line := fmt.Sprintf("%s#%-3d %s %-*s%s", cursor, t.ID, status, width-20, truncate(t.Title, width-20), agentStr)
		lines = append(lines, line)
	}

	return strings.Join(lines, "\n")
}

func (m Model) renderEvents(width, height int) string {
	header := lipgloss.NewStyle().Bold(true).Render("Events")
	var lines []string
	lines = append(lines, header)

	if len(m.events) == 0 {
		lines = append(lines, "  (no events)")
	}

	start := m.eventScroll
	end := start + height
	if end > len(m.events) {
		end = len(m.events)
	}

	for _, e := range m.events[start:end] {
		ts := ""
		if len(e.CreatedAt) >= 16 {
			ts = e.CreatedAt[11:16]
		}
		icon := kindIcon(e.Kind)
		line := fmt.Sprintf("  %s %s %s", ts, icon, truncate(e.Message, width-15))
		lines = append(lines, line)
	}

	return strings.Join(lines, "\n")
}

func statusBadge(status string) string {
	switch status {
	case "queued":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("245")).Render("[QUEUED]")
	case "dispatched", "running":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Bold(true).Render("[RUNNING]")
	case "done":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("[DONE]")
	case "failed":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("196")).Render("[FAILED]")
	case "timeout":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("208")).Render("[TIMEOUT]")
	default:
		return fmt.Sprintf("[%s]", strings.ToUpper(status))
	}
}

func kindIcon(kind string) string {
	switch kind {
	case "completed":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("42")).Render("OK")
	case "failed":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("196")).Render("!!")
	case "dispatched":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Render(">>")
	case "retry":
		return lipgloss.NewStyle().Foreground(lipgloss.Color("208")).Render("<>")
	default:
		return "--"
	}
}

func truncate(s string, maxLen int) string {
	if maxLen <= 0 {
		return ""
	}
	if len(s) <= maxLen {
		return s
	}
	if maxLen <= 3 {
		return s[:maxLen]
	}
	return s[:maxLen-3] + "..."
}

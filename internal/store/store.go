package store

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

// DB wraps a sql.DB connection to the yeehaw SQLite database.
type DB struct {
	*sql.DB
}

// DefaultPath returns the default database path: .yeehaw/yeehaw.db in the
// current working directory.
func DefaultPath() string {
	return filepath.Join(".yeehaw", "yeehaw.db")
}

// Open opens (or creates) the SQLite database at the given path.
func Open(path string) (*DB, error) {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, fmt.Errorf("create db dir: %w", err)
	}

	db, err := sql.Open("sqlite", path+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, fmt.Errorf("open db: %w", err)
	}
	db.SetMaxOpenConns(1) // SQLite is single-writer
	return &DB{db}, nil
}

// InitDB runs the schema DDL to create all tables.
func (db *DB) InitDB() error {
	_, err := db.Exec(ddl)
	if err != nil {
		return fmt.Errorf("init schema: %w", err)
	}
	return nil
}

// Project represents a row in the projects table.
type Project struct {
	ID         int64
	Name       string
	RootPath   string
	Guidelines string
	GitRemote  string
	MainBranch string
	CreatedAt  string
	UpdatedAt  string
}

// AddProject inserts a new project.
func (db *DB) AddProject(name, rootPath string) (*Project, error) {
	abs, err := filepath.Abs(rootPath)
	if err != nil {
		return nil, fmt.Errorf("resolve path: %w", err)
	}
	res, err := db.Exec(
		`INSERT INTO projects (name, root_path) VALUES (?, ?)`,
		name, abs,
	)
	if err != nil {
		return nil, fmt.Errorf("insert project: %w", err)
	}
	id, _ := res.LastInsertId()
	return &Project{ID: id, Name: name, RootPath: abs}, nil
}

// ListProjects returns all projects.
func (db *DB) ListProjects() ([]Project, error) {
	rows, err := db.Query(`SELECT id, name, root_path, guidelines, git_remote, main_branch, created_at, updated_at FROM projects ORDER BY name`)
	if err != nil {
		return nil, fmt.Errorf("list projects: %w", err)
	}
	defer rows.Close()

	var projects []Project
	for rows.Next() {
		var p Project
		if err := rows.Scan(&p.ID, &p.Name, &p.RootPath, &p.Guidelines, &p.GitRemote, &p.MainBranch, &p.CreatedAt, &p.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan project: %w", err)
		}
		projects = append(projects, p)
	}
	return projects, rows.Err()
}

// GetProject retrieves a project by name.
func (db *DB) GetProject(name string) (*Project, error) {
	var p Project
	err := db.QueryRow(
		`SELECT id, name, root_path, guidelines, git_remote, main_branch, created_at, updated_at FROM projects WHERE name = ?`,
		name,
	).Scan(&p.ID, &p.Name, &p.RootPath, &p.Guidelines, &p.GitRemote, &p.MainBranch, &p.CreatedAt, &p.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, fmt.Errorf("project %q not found", name)
	}
	if err != nil {
		return nil, fmt.Errorf("get project: %w", err)
	}
	return &p, nil
}

// Task represents a row in the tasks table.
type Task struct {
	ID           int64
	PhaseID      int64
	Number       string
	Title        string
	Description  string
	Status       string
	Agent        string
	Branch       string
	WorktreePath string
	SignalDir    string
	AttemptCount int
	MaxAttempts  int
	StartedAt    *string
	FinishedAt   *string
}

// GetQueuedTasks returns tasks with status 'queued' for a given roadmap.
func (db *DB) GetQueuedTasks(roadmapID int64) ([]Task, error) {
	rows, err := db.Query(`
		SELECT t.id, t.phase_id, t.number, t.title, t.description, t.status, t.agent,
		       t.branch, t.worktree_path, t.signal_dir, t.attempt_count, t.max_attempts,
		       t.started_at, t.finished_at
		FROM tasks t
		JOIN roadmap_phases rp ON t.phase_id = rp.id
		WHERE rp.roadmap_id = ? AND t.status = 'queued'
		ORDER BY t.id
	`, roadmapID)
	if err != nil {
		return nil, fmt.Errorf("get queued tasks: %w", err)
	}
	defer rows.Close()
	return scanTasks(rows)
}

// GetRunningTasks returns tasks with status 'running' or 'dispatched'.
func (db *DB) GetRunningTasks(roadmapID int64) ([]Task, error) {
	rows, err := db.Query(`
		SELECT t.id, t.phase_id, t.number, t.title, t.description, t.status, t.agent,
		       t.branch, t.worktree_path, t.signal_dir, t.attempt_count, t.max_attempts,
		       t.started_at, t.finished_at
		FROM tasks t
		JOIN roadmap_phases rp ON t.phase_id = rp.id
		WHERE rp.roadmap_id = ? AND t.status IN ('dispatched', 'running')
		ORDER BY t.id
	`, roadmapID)
	if err != nil {
		return nil, fmt.Errorf("get running tasks: %w", err)
	}
	defer rows.Close()
	return scanTasks(rows)
}

// UpdateTaskStatus updates a task's status and optionally sets timestamps.
func (db *DB) UpdateTaskStatus(taskID int64, status string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	var err error
	switch status {
	case "running", "dispatched":
		_, err = db.Exec(`UPDATE tasks SET status = ?, started_at = ?, updated_at = ? WHERE id = ?`, status, now, now, taskID)
	case "done", "failed", "timeout", "skipped":
		_, err = db.Exec(`UPDATE tasks SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?`, status, now, now, taskID)
	default:
		_, err = db.Exec(`UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?`, status, now, taskID)
	}
	if err != nil {
		return fmt.Errorf("update task %d status: %w", taskID, err)
	}
	return nil
}

// UpdateTaskAgent sets the agent and branch for a task.
func (db *DB) UpdateTaskAgent(taskID int64, agent, branch, worktreePath, signalDir string) error {
	_, err := db.Exec(
		`UPDATE tasks SET agent = ?, branch = ?, worktree_path = ?, signal_dir = ?, updated_at = datetime('now') WHERE id = ?`,
		agent, branch, worktreePath, signalDir, taskID,
	)
	return err
}

// IncrementAttempt increments the attempt count for a task.
func (db *DB) IncrementAttempt(taskID int64) error {
	_, err := db.Exec(`UPDATE tasks SET attempt_count = attempt_count + 1, updated_at = datetime('now') WHERE id = ?`, taskID)
	return err
}

// InsertEvent logs an event.
func (db *DB) InsertEvent(projectID, taskID *int64, kind, message string) error {
	_, err := db.Exec(
		`INSERT INTO events (project_id, task_id, kind, message) VALUES (?, ?, ?, ?)`,
		projectID, taskID, kind, message,
	)
	return err
}

// RecentEvents returns the last N events.
func (db *DB) RecentEvents(limit int) ([]Event, error) {
	rows, err := db.Query(`SELECT id, project_id, task_id, kind, message, created_at FROM events ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var events []Event
	for rows.Next() {
		var e Event
		if err := rows.Scan(&e.ID, &e.ProjectID, &e.TaskID, &e.Kind, &e.Message, &e.CreatedAt); err != nil {
			return nil, err
		}
		events = append(events, e)
	}
	return events, rows.Err()
}

// Event represents a row in the events table.
type Event struct {
	ID        int64
	ProjectID *int64
	TaskID    *int64
	Kind      string
	Message   string
	CreatedAt string
}

// SchedulerConfig represents the scheduler_config singleton row.
type SchedulerConfig struct {
	MaxGlobal      int
	MaxPerProject  int
	TimeoutMinutes int
}

// GetSchedulerConfig returns the current scheduler configuration.
func (db *DB) GetSchedulerConfig() (*SchedulerConfig, error) {
	var c SchedulerConfig
	err := db.QueryRow(`SELECT max_global, max_per_project, timeout_minutes FROM scheduler_config WHERE id = 1`).
		Scan(&c.MaxGlobal, &c.MaxPerProject, &c.TimeoutMinutes)
	if err != nil {
		return nil, fmt.Errorf("get scheduler config: %w", err)
	}
	return &c, nil
}

// UpdateSchedulerConfig updates scheduler limits.
func (db *DB) UpdateSchedulerConfig(maxGlobal, maxPerProject, timeoutMinutes *int) error {
	if maxGlobal != nil {
		if _, err := db.Exec(`UPDATE scheduler_config SET max_global = ?, updated_at = datetime('now') WHERE id = 1`, *maxGlobal); err != nil {
			return err
		}
	}
	if maxPerProject != nil {
		if _, err := db.Exec(`UPDATE scheduler_config SET max_per_project = ?, updated_at = datetime('now') WHERE id = 1`, *maxPerProject); err != nil {
			return err
		}
	}
	if timeoutMinutes != nil {
		if _, err := db.Exec(`UPDATE scheduler_config SET timeout_minutes = ?, updated_at = datetime('now') WHERE id = 1`, *timeoutMinutes); err != nil {
			return err
		}
	}
	return nil
}

// Roadmap represents a row in the roadmaps table.
type Roadmap struct {
	ID        int64
	ProjectID int64
	RawText   string
	Status    string
	CreatedAt string
	UpdatedAt string
}

// InsertRoadmap stores a new roadmap.
func (db *DB) InsertRoadmap(projectID int64, rawText, status string) (int64, error) {
	res, err := db.Exec(`INSERT INTO roadmaps (project_id, raw_text, status) VALUES (?, ?, ?)`, projectID, rawText, status)
	if err != nil {
		return 0, fmt.Errorf("insert roadmap: %w", err)
	}
	return res.LastInsertId()
}

// GetLatestRoadmap returns the most recent roadmap for a project.
func (db *DB) GetLatestRoadmap(projectID int64) (*Roadmap, error) {
	var r Roadmap
	err := db.QueryRow(
		`SELECT id, project_id, raw_text, status, created_at, updated_at FROM roadmaps WHERE project_id = ? ORDER BY id DESC LIMIT 1`,
		projectID,
	).Scan(&r.ID, &r.ProjectID, &r.RawText, &r.Status, &r.CreatedAt, &r.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get latest roadmap: %w", err)
	}
	return &r, nil
}

// UpdateRoadmapStatus sets the roadmap status.
func (db *DB) UpdateRoadmapStatus(roadmapID int64, status string) error {
	_, err := db.Exec(`UPDATE roadmaps SET status = ?, updated_at = datetime('now') WHERE id = ?`, status, roadmapID)
	return err
}

// RoadmapPhase represents a row in the roadmap_phases table.
type RoadmapPhase struct {
	ID               int64
	RoadmapID        int64
	Number           int
	Title            string
	VerificationText string
	Status           string
}

// InsertPhase inserts a roadmap phase.
func (db *DB) InsertPhase(roadmapID int64, number int, title, verification string) (int64, error) {
	res, err := db.Exec(
		`INSERT INTO roadmap_phases (roadmap_id, number, title, verification_text) VALUES (?, ?, ?, ?)`,
		roadmapID, number, title, verification,
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// InsertTask inserts a task into a phase.
func (db *DB) InsertTask(phaseID int64, number, title, description string) (int64, error) {
	res, err := db.Exec(
		`INSERT INTO tasks (phase_id, number, title, description) VALUES (?, ?, ?, ?)`,
		phaseID, number, title, description,
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// InsertWorktree records a git worktree.
func (db *DB) InsertWorktree(taskID int64, path, branch, baseSHA string) (int64, error) {
	res, err := db.Exec(
		`INSERT INTO git_worktrees (task_id, path, branch, base_sha) VALUES (?, ?, ?, ?)`,
		taskID, path, branch, baseSHA,
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// CountRunningTasks returns the total number of dispatched/running tasks globally.
func (db *DB) CountRunningTasks() (int, error) {
	var count int
	err := db.QueryRow(`SELECT COUNT(*) FROM tasks WHERE status IN ('dispatched', 'running')`).Scan(&count)
	return count, err
}

// CountRunningTasksForProject returns running tasks for a specific project.
func (db *DB) CountRunningTasksForProject(projectID int64) (int, error) {
	var count int
	err := db.QueryRow(`
		SELECT COUNT(*) FROM tasks t
		JOIN roadmap_phases rp ON t.phase_id = rp.id
		JOIN roadmaps r ON rp.roadmap_id = r.id
		WHERE r.project_id = ? AND t.status IN ('dispatched', 'running')
	`, projectID).Scan(&count)
	return count, err
}

// GetTask retrieves a single task by ID.
func (db *DB) GetTask(taskID int64) (*Task, error) {
	row := db.QueryRow(`
		SELECT id, phase_id, number, title, description, status, agent,
		       branch, worktree_path, signal_dir, attempt_count, max_attempts,
		       started_at, finished_at
		FROM tasks WHERE id = ?
	`, taskID)
	var t Task
	err := row.Scan(&t.ID, &t.PhaseID, &t.Number, &t.Title, &t.Description, &t.Status, &t.Agent,
		&t.Branch, &t.WorktreePath, &t.SignalDir, &t.AttemptCount, &t.MaxAttempts,
		&t.StartedAt, &t.FinishedAt)
	if err == sql.ErrNoRows {
		return nil, fmt.Errorf("task %d not found", taskID)
	}
	if err != nil {
		return nil, err
	}
	return &t, nil
}

func scanTasks(rows *sql.Rows) ([]Task, error) {
	var tasks []Task
	for rows.Next() {
		var t Task
		if err := rows.Scan(&t.ID, &t.PhaseID, &t.Number, &t.Title, &t.Description, &t.Status, &t.Agent,
			&t.Branch, &t.WorktreePath, &t.SignalDir, &t.AttemptCount, &t.MaxAttempts,
			&t.StartedAt, &t.FinishedAt); err != nil {
			return nil, err
		}
		tasks = append(tasks, t)
	}
	return tasks, rows.Err()
}

package orchestrator

import (
	"fmt"
	"log"
	"time"

	"github.com/aescoubas/yeehaw/internal/agent"
	gitpkg "github.com/aescoubas/yeehaw/internal/git"
	"github.com/aescoubas/yeehaw/internal/signal"
	"github.com/aescoubas/yeehaw/internal/store"
	"github.com/aescoubas/yeehaw/internal/tmux"
)

// Orchestrator manages the dispatch and monitoring of tasks.
type Orchestrator struct {
	DB       *store.DB
	Watcher  *signal.Watcher
	Roadmap  *store.Roadmap
	Project  *store.Project
	Logger   *log.Logger
	TickRate time.Duration

	stopCh chan struct{}
}

// New creates a new Orchestrator.
func New(db *store.DB, project *store.Project, roadmap *store.Roadmap, logger *log.Logger) (*Orchestrator, error) {
	w, err := signal.NewWatcher()
	if err != nil {
		return nil, fmt.Errorf("create watcher: %w", err)
	}
	return &Orchestrator{
		DB:       db,
		Watcher:  w,
		Roadmap:  roadmap,
		Project:  project,
		Logger:   logger,
		TickRate: 5 * time.Second,
		stopCh:   make(chan struct{}),
	}, nil
}

// Tick runs a single orchestration cycle: monitor active tasks, dispatch queued.
func (o *Orchestrator) Tick() error {
	if err := o.monitorActive(); err != nil {
		o.Logger.Printf("monitor error: %v", err)
	}
	if err := o.dispatchQueued(); err != nil {
		o.Logger.Printf("dispatch error: %v", err)
	}
	return nil
}

// RunForever starts the tick loop. Blocks until Stop() is called.
func (o *Orchestrator) RunForever() error {
	o.Watcher.Start()
	defer o.Watcher.Stop()

	ticker := time.NewTicker(o.TickRate)
	defer ticker.Stop()

	// Initial tick
	o.Tick()

	for {
		select {
		case <-o.stopCh:
			return nil
		case evt := <-o.Watcher.Events():
			o.handleSignalEvent(evt)
		case <-ticker.C:
			o.Tick()
		}
	}
}

// Stop signals the orchestrator to shut down.
func (o *Orchestrator) Stop() {
	close(o.stopCh)
}

func (o *Orchestrator) monitorActive() error {
	tasks, err := o.DB.GetRunningTasks(o.Roadmap.ID)
	if err != nil {
		return err
	}

	cfg, err := o.DB.GetSchedulerConfig()
	if err != nil {
		return err
	}

	for _, task := range tasks {
		// Check tmux session alive
		sessName := tmux.SessionName(task.ID)
		if !tmux.HasSession(sessName) {
			o.Logger.Printf("task %d: tmux session gone, checking for late signal", task.ID)
			// Check for late signal
			sig, err := signal.CheckSignalFile(o.Project.RootPath, task.ID)
			if err == nil && sig != nil {
				o.handleCompletion(task, sig)
				continue
			}
			o.handleFailure(task, "tmux session died unexpectedly")
			continue
		}

		// Check timeout
		if task.StartedAt != nil {
			started, err := time.Parse(time.RFC3339, *task.StartedAt)
			if err == nil {
				timeout := time.Duration(cfg.TimeoutMinutes) * time.Minute
				if time.Since(started) > timeout {
					o.Logger.Printf("task %d: timeout after %v", task.ID, timeout)
					tmux.KillSession(sessName)
					o.handleFailure(task, fmt.Sprintf("timeout after %d minutes", cfg.TimeoutMinutes))
					continue
				}
			}
		}

		// Check signal via polling fallback
		sig, err := signal.CheckSignalFile(o.Project.RootPath, task.ID)
		if err == nil && sig != nil {
			o.handleCompletion(task, sig)
		}
	}

	return nil
}

func (o *Orchestrator) dispatchQueued() error {
	cfg, err := o.DB.GetSchedulerConfig()
	if err != nil {
		return err
	}

	// Check global limit
	globalRunning, err := o.DB.CountRunningTasks()
	if err != nil {
		return err
	}
	if globalRunning >= cfg.MaxGlobal {
		return nil
	}

	// Check per-project limit
	projectRunning, err := o.DB.CountRunningTasksForProject(o.Project.ID)
	if err != nil {
		return err
	}
	if projectRunning >= cfg.MaxPerProject {
		return nil
	}

	tasks, err := o.DB.GetQueuedTasks(o.Roadmap.ID)
	if err != nil {
		return err
	}

	available := min(cfg.MaxGlobal-globalRunning, cfg.MaxPerProject-projectRunning)

	for i, task := range tasks {
		if i >= available {
			break
		}
		if err := o.dispatchTask(task); err != nil {
			o.Logger.Printf("dispatch task %d failed: %v", task.ID, err)
			o.DB.InsertEvent(&o.Project.ID, &task.ID, "dispatch_error", err.Error())
			continue
		}
	}

	return nil
}

func (o *Orchestrator) dispatchTask(task store.Task) error {
	// Choose agent (use task's assigned agent or default to claude)
	agentName := task.Agent
	if agentName == "" {
		agentName = "claude"
	}
	profile, err := agent.Resolve(agentName)
	if err != nil {
		return err
	}

	// Create git worktree
	branch := gitpkg.BranchForTask(task.Number, task.Title)
	worktreePath, baseSHA, err := gitpkg.PrepareWorktree(o.Project.RootPath, branch)
	if err != nil {
		return fmt.Errorf("prepare worktree: %w", err)
	}

	// Create signal directory
	sigDir, err := signal.EnsureSignalDir(o.Project.RootPath, task.ID)
	if err != nil {
		gitpkg.CleanupWorktree(o.Project.RootPath, worktreePath)
		return fmt.Errorf("create signal dir: %w", err)
	}

	// Record worktree in DB
	if _, err := o.DB.InsertWorktree(task.ID, worktreePath, branch, baseSHA); err != nil {
		o.Logger.Printf("warning: failed to record worktree: %v", err)
	}

	// Update task with agent info
	if err := o.DB.UpdateTaskAgent(task.ID, agentName, branch, worktreePath, sigDir); err != nil {
		return err
	}

	// Watch signal directory
	if err := o.Watcher.Watch(sigDir, task.ID); err != nil {
		o.Logger.Printf("warning: could not watch signal dir: %v", err)
	}

	// Build prompt
	prompt := agent.BuildTaskPrompt(task.Number, task.Title, task.Description, sigDir, "", "")

	// Create tmux session and launch agent
	sessName := tmux.SessionName(task.ID)
	if err := tmux.EnsureSession(sessName, worktreePath); err != nil {
		return fmt.Errorf("create tmux session: %w", err)
	}

	cmd := agent.ResolveCommand(profile, prompt)
	if err := tmux.SendText(sessName, cmd); err != nil {
		return fmt.Errorf("send command to tmux: %w", err)
	}

	// Update status
	if err := o.DB.UpdateTaskStatus(task.ID, "dispatched"); err != nil {
		return err
	}
	if err := o.DB.IncrementAttempt(task.ID); err != nil {
		return err
	}

	o.Logger.Printf("task %d dispatched to %s (branch: %s)", task.ID, agentName, branch)
	o.DB.InsertEvent(&o.Project.ID, &task.ID, "dispatched", fmt.Sprintf("dispatched to %s", agentName))

	return nil
}

func (o *Orchestrator) handleSignalEvent(evt signal.WatchEvent) {
	if evt.Err != nil {
		o.Logger.Printf("signal watcher error: %v", evt.Err)
		return
	}

	task, err := o.DB.GetTask(evt.TaskID)
	if err != nil {
		o.Logger.Printf("signal for unknown task %d: %v", evt.TaskID, err)
		return
	}

	o.handleCompletion(*task, evt.Signal)
}

func (o *Orchestrator) handleCompletion(task store.Task, sig *signal.Signal) {
	sessName := tmux.SessionName(task.ID)
	tmux.KillSession(sessName)
	o.Watcher.Unwatch(task.SignalDir)

	switch sig.Status {
	case "done":
		o.Logger.Printf("task %d completed: %s", task.ID, sig.Summary)
		o.DB.UpdateTaskStatus(task.ID, "done")
		o.DB.InsertEvent(&o.Project.ID, &task.ID, "completed", sig.Summary)

	case "failed":
		o.handleFailure(task, sig.Summary)

	case "blocked":
		o.Logger.Printf("task %d blocked: %s", task.ID, sig.Summary)
		o.DB.UpdateTaskStatus(task.ID, "failed")
		o.DB.InsertEvent(&o.Project.ID, &task.ID, "blocked", sig.Summary)

	default:
		o.Logger.Printf("task %d unknown signal status: %s", task.ID, sig.Status)
		o.handleFailure(task, fmt.Sprintf("unknown signal status: %s", sig.Status))
	}
}

func (o *Orchestrator) handleFailure(task store.Task, reason string) {
	o.Logger.Printf("task %d failed: %s (attempt %d/%d)", task.ID, reason, task.AttemptCount, task.MaxAttempts)

	sessName := tmux.SessionName(task.ID)
	tmux.KillSession(sessName)
	o.Watcher.Unwatch(task.SignalDir)

	if task.AttemptCount < task.MaxAttempts {
		// Re-queue for retry
		o.DB.UpdateTaskStatus(task.ID, "queued")
		o.DB.InsertEvent(&o.Project.ID, &task.ID, "retry", fmt.Sprintf("retrying: %s", reason))
	} else {
		o.DB.UpdateTaskStatus(task.ID, "failed")
		o.DB.InsertEvent(&o.Project.ID, &task.ID, "failed", fmt.Sprintf("max attempts reached: %s", reason))
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

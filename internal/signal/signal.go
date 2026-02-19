package signal

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"
)

// Signal is the JSON structure agents write to indicate task completion.
type Signal struct {
	TaskID    string   `json:"task_id"`
	Status    string   `json:"status"` // "done", "failed", "blocked"
	Summary   string   `json:"summary"`
	Artifacts []string `json:"artifacts"`
	Timestamp string   `json:"timestamp"`
}

// SignalDir returns the signal directory path for a task.
func SignalDir(repoRoot string, taskID int64) string {
	return filepath.Join(repoRoot, ".yeehaw", "signals", fmt.Sprintf("task-%d", taskID))
}

// SignalFile returns the full signal file path for a task.
func SignalFile(repoRoot string, taskID int64) string {
	return filepath.Join(SignalDir(repoRoot, taskID), "signal.json")
}

// EnsureSignalDir creates the signal directory for a task.
func EnsureSignalDir(repoRoot string, taskID int64) (string, error) {
	dir := SignalDir(repoRoot, taskID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", fmt.Errorf("create signal dir: %w", err)
	}
	return dir, nil
}

// ReadSignal reads and parses a signal file. Retries up to 3 times with 200ms
// intervals to handle partial writes.
func ReadSignal(path string) (*Signal, error) {
	var lastErr error
	for i := 0; i < 3; i++ {
		data, err := os.ReadFile(path)
		if err != nil {
			lastErr = err
			time.Sleep(200 * time.Millisecond)
			continue
		}
		var sig Signal
		if err := json.Unmarshal(data, &sig); err != nil {
			lastErr = fmt.Errorf("parse signal: %w", err)
			time.Sleep(200 * time.Millisecond)
			continue
		}
		return &sig, nil
	}
	return nil, fmt.Errorf("read signal after 3 retries: %w", lastErr)
}

// CheckSignalFile does a one-shot check for a signal file (no fsnotify).
func CheckSignalFile(repoRoot string, taskID int64) (*Signal, error) {
	path := SignalFile(repoRoot, taskID)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, nil // no signal yet
	}
	return ReadSignal(path)
}

// Watcher watches multiple signal directories for signal.json creation.
type Watcher struct {
	watcher    *fsnotify.Watcher
	signals    chan WatchEvent
	mu         sync.Mutex
	dirs       map[string]int64 // dir -> taskID
	debounce   time.Duration
	stopCh     chan struct{}
}

// WatchEvent delivers a parsed signal along with the task ID.
type WatchEvent struct {
	TaskID int64
	Signal *Signal
	Err    error
}

// NewWatcher creates a new signal watcher.
func NewWatcher() (*Watcher, error) {
	fw, err := fsnotify.NewWatcher()
	if err != nil {
		return nil, fmt.Errorf("create fsnotify watcher: %w", err)
	}
	return &Watcher{
		watcher:  fw,
		signals:  make(chan WatchEvent, 32),
		dirs:     make(map[string]int64),
		debounce: 500 * time.Millisecond,
		stopCh:   make(chan struct{}),
	}, nil
}

// Watch adds a signal directory to the watch list.
func (w *Watcher) Watch(dir string, taskID int64) error {
	w.mu.Lock()
	defer w.mu.Unlock()

	if err := w.watcher.Add(dir); err != nil {
		return fmt.Errorf("watch %s: %w", dir, err)
	}
	w.dirs[dir] = taskID
	return nil
}

// Unwatch removes a signal directory from the watch list.
func (w *Watcher) Unwatch(dir string) {
	w.mu.Lock()
	defer w.mu.Unlock()

	_ = w.watcher.Remove(dir)
	delete(w.dirs, dir)
}

// Events returns the channel for receiving signal events.
func (w *Watcher) Events() <-chan WatchEvent {
	return w.signals
}

// Start begins the event processing loop in a goroutine.
func (w *Watcher) Start() {
	go w.loop()
}

// Stop terminates the watcher.
func (w *Watcher) Stop() {
	close(w.stopCh)
	w.watcher.Close()
}

func (w *Watcher) loop() {
	// Track pending debounces per directory
	pending := make(map[string]*time.Timer)

	for {
		select {
		case <-w.stopCh:
			return

		case event, ok := <-w.watcher.Events:
			if !ok {
				return
			}
			// Only care about signal.json files
			if filepath.Base(event.Name) != "signal.json" {
				continue
			}
			if event.Op&(fsnotify.Create|fsnotify.Write) == 0 {
				continue
			}

			dir := filepath.Dir(event.Name)

			// Debounce: reset timer if already pending
			if t, exists := pending[dir]; exists {
				t.Stop()
			}
			pending[dir] = time.AfterFunc(w.debounce, func() {
				w.mu.Lock()
				taskID, ok := w.dirs[dir]
				w.mu.Unlock()
				if !ok {
					return
				}

				sig, err := ReadSignal(event.Name)
				w.signals <- WatchEvent{
					TaskID: taskID,
					Signal: sig,
					Err:    err,
				}
			})

		case err, ok := <-w.watcher.Errors:
			if !ok {
				return
			}
			w.signals <- WatchEvent{Err: fmt.Errorf("fsnotify error: %w", err)}
		}
	}
}

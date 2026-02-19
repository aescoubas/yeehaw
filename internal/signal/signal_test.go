package signal

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestReadSignal(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "signal.json")

	sig := Signal{
		TaskID:    "1.1",
		Status:    "done",
		Summary:   "test completed",
		Artifacts: []string{"main.go"},
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}

	data, _ := json.Marshal(sig)
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}

	got, err := ReadSignal(path)
	if err != nil {
		t.Fatalf("ReadSignal: %v", err)
	}
	if got.Status != "done" {
		t.Errorf("status: got %q, want done", got.Status)
	}
	if got.Summary != "test completed" {
		t.Errorf("summary: got %q", got.Summary)
	}
}

func TestCheckSignalFileMissing(t *testing.T) {
	dir := t.TempDir()
	sig, err := CheckSignalFile(dir, 999)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sig != nil {
		t.Error("expected nil for missing signal")
	}
}

func TestWatcher(t *testing.T) {
	dir := t.TempDir()
	sigDir := filepath.Join(dir, ".yeehaw", "signals", "task-1")
	if err := os.MkdirAll(sigDir, 0o755); err != nil {
		t.Fatal(err)
	}

	w, err := NewWatcher()
	if err != nil {
		t.Fatalf("NewWatcher: %v", err)
	}
	defer w.Stop()

	if err := w.Watch(sigDir, 1); err != nil {
		t.Fatalf("Watch: %v", err)
	}

	w.Start()

	// Write signal file after a short delay
	go func() {
		time.Sleep(100 * time.Millisecond)
		sig := Signal{TaskID: "1", Status: "done", Summary: "ok"}
		data, _ := json.Marshal(sig)
		os.WriteFile(filepath.Join(sigDir, "signal.json"), data, 0o644)
	}()

	select {
	case evt := <-w.Events():
		if evt.Err != nil {
			t.Fatalf("watcher error: %v", evt.Err)
		}
		if evt.TaskID != 1 {
			t.Errorf("taskID: got %d, want 1", evt.TaskID)
		}
		if evt.Signal.Status != "done" {
			t.Errorf("status: got %q, want done", evt.Signal.Status)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("timeout waiting for signal event")
	}
}

func TestSignalDir(t *testing.T) {
	got := SignalDir("/repo", 42)
	want := "/repo/.yeehaw/signals/task-42"
	if got != want {
		t.Errorf("SignalDir: got %q, want %q", got, want)
	}
}

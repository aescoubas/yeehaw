package store

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInitAndCRUD(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "test.db")

	db, err := Open(dbPath)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer db.Close()

	if err := db.InitDB(); err != nil {
		t.Fatalf("InitDB: %v", err)
	}

	// Idempotent init
	if err := db.InitDB(); err != nil {
		t.Fatalf("InitDB (2nd): %v", err)
	}

	// Add project
	p, err := db.AddProject("myapp", dir)
	if err != nil {
		t.Fatalf("AddProject: %v", err)
	}
	if p.Name != "myapp" {
		t.Errorf("got name %q, want myapp", p.Name)
	}

	// List projects
	projects, err := db.ListProjects()
	if err != nil {
		t.Fatalf("ListProjects: %v", err)
	}
	if len(projects) != 1 {
		t.Fatalf("got %d projects, want 1", len(projects))
	}

	// Get project
	got, err := db.GetProject("myapp")
	if err != nil {
		t.Fatalf("GetProject: %v", err)
	}
	if got.ID != p.ID {
		t.Errorf("got ID %d, want %d", got.ID, p.ID)
	}

	// Scheduler config
	cfg, err := db.GetSchedulerConfig()
	if err != nil {
		t.Fatalf("GetSchedulerConfig: %v", err)
	}
	if cfg.MaxGlobal != 5 || cfg.MaxPerProject != 3 || cfg.TimeoutMinutes != 60 {
		t.Errorf("unexpected defaults: %+v", cfg)
	}

	newMax := 10
	if err := db.UpdateSchedulerConfig(&newMax, nil, nil); err != nil {
		t.Fatalf("UpdateSchedulerConfig: %v", err)
	}
	cfg, _ = db.GetSchedulerConfig()
	if cfg.MaxGlobal != 10 {
		t.Errorf("got MaxGlobal %d, want 10", cfg.MaxGlobal)
	}

	// Roadmap + Phase + Task
	rmID, err := db.InsertRoadmap(p.ID, "# Roadmap", "draft")
	if err != nil {
		t.Fatalf("InsertRoadmap: %v", err)
	}

	phID, err := db.InsertPhase(rmID, 1, "Setup", "go test ./...")
	if err != nil {
		t.Fatalf("InsertPhase: %v", err)
	}

	taskID, err := db.InsertTask(phID, "1.1", "Init project", "Initialize the project")
	if err != nil {
		t.Fatalf("InsertTask: %v", err)
	}

	task, err := db.GetTask(taskID)
	if err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if task.Status != "queued" {
		t.Errorf("got status %q, want queued", task.Status)
	}

	// Events
	pid := p.ID
	if err := db.InsertEvent(&pid, &taskID, "test", "hello"); err != nil {
		t.Fatalf("InsertEvent: %v", err)
	}
	events, err := db.RecentEvents(10)
	if err != nil {
		t.Fatalf("RecentEvents: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("got %d events, want 1", len(events))
	}

	// Cleanup
	os.Remove(dbPath)
}

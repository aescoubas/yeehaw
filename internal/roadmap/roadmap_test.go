package roadmap

import (
	"strings"
	"testing"
)

const sampleRoadmap = `# Roadmap: myapp

## Phase 1: Foundation
**Verification:** ` + "`go test ./...`" + `

### Task 1.1: Init project
Set up the Go module and directory structure.

### Task 1.2: Add database
Create SQLite schema and basic CRUD.

## Phase 2: Features
**Verification:** ` + "`go test ./... && go vet ./...`" + `

### Task 2.1: Add API
Build HTTP API endpoints.
`

func TestParse(t *testing.T) {
	rm, err := Parse(sampleRoadmap)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}

	if rm.ProjectName != "myapp" {
		t.Errorf("project name: got %q, want myapp", rm.ProjectName)
	}

	if len(rm.Phases) != 2 {
		t.Fatalf("phases: got %d, want 2", len(rm.Phases))
	}

	p1 := rm.Phases[0]
	if p1.Number != 1 || p1.Title != "Foundation" {
		t.Errorf("phase 1: got (%d, %q)", p1.Number, p1.Title)
	}
	if p1.Verification != "go test ./..." {
		t.Errorf("phase 1 verification: got %q", p1.Verification)
	}
	if len(p1.Tasks) != 2 {
		t.Fatalf("phase 1 tasks: got %d, want 2", len(p1.Tasks))
	}
	if p1.Tasks[0].Number != "1.1" || p1.Tasks[0].Title != "Init project" {
		t.Errorf("task 1.1: got (%q, %q)", p1.Tasks[0].Number, p1.Tasks[0].Title)
	}
	if !strings.Contains(p1.Tasks[0].Description, "Go module") {
		t.Errorf("task 1.1 description missing content: %q", p1.Tasks[0].Description)
	}

	p2 := rm.Phases[1]
	if len(p2.Tasks) != 1 {
		t.Fatalf("phase 2 tasks: got %d, want 1", len(p2.Tasks))
	}
}

func TestValidate(t *testing.T) {
	rm, _ := Parse(sampleRoadmap)
	errs := Validate(rm)
	if len(errs) != 0 {
		t.Errorf("expected no errors, got: %v", errs)
	}
}

func TestValidateEmpty(t *testing.T) {
	rm := &Roadmap{}
	errs := Validate(rm)
	if len(errs) < 2 {
		t.Errorf("expected at least 2 errors for empty roadmap, got %d: %v", len(errs), errs)
	}
}

func TestValidateBadNumbering(t *testing.T) {
	bad := `# Roadmap: test

## Phase 1: First

### Task 1.1: A
Desc.

### Task 1.3: B
Desc.
`
	rm, err := Parse(bad)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	errs := Validate(rm)
	found := false
	for _, e := range errs {
		if strings.Contains(e, "unexpected number") {
			found = true
		}
	}
	if !found {
		t.Errorf("expected numbering error, got: %v", errs)
	}
}

package roadmap

import (
	"fmt"
	"regexp"
	"strings"
)

// Roadmap is the top-level parsed structure.
type Roadmap struct {
	ProjectName string
	Phases      []Phase
}

// Phase represents a phase in the roadmap.
type Phase struct {
	Number       int
	Title        string
	Verification string
	Tasks        []Task
}

// Task represents a single task within a phase.
type Task struct {
	Number      string // e.g. "1.1"
	Title       string
	Description string
}

var (
	reH1    = regexp.MustCompile(`^#\s+Roadmap:\s*(.+)`)
	reH2    = regexp.MustCompile(`^##\s+Phase\s+(\d+):\s*(.+)`)
	reH3    = regexp.MustCompile(`^###\s+Task\s+(\d+\.\d+):\s*(.+)`)
	reVerif = regexp.MustCompile(`^\*\*Verification:\*\*\s*` + "`" + `(.+)` + "`")
)

// Parse parses a markdown roadmap into a structured Roadmap.
func Parse(text string) (*Roadmap, error) {
	lines := strings.Split(text, "\n")
	rm := &Roadmap{}

	var currentPhase *Phase
	var currentTask *Task
	var descLines []string

	flushTask := func() {
		if currentTask != nil {
			currentTask.Description = strings.TrimSpace(strings.Join(descLines, "\n"))
			currentPhase.Tasks = append(currentPhase.Tasks, *currentTask)
			currentTask = nil
			descLines = nil
		}
	}

	flushPhase := func() {
		flushTask()
		if currentPhase != nil {
			rm.Phases = append(rm.Phases, *currentPhase)
			currentPhase = nil
		}
	}

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)

		// H1: Roadmap title
		if m := reH1.FindStringSubmatch(trimmed); m != nil {
			rm.ProjectName = strings.TrimSpace(m[1])
			continue
		}

		// H2: Phase
		if m := reH2.FindStringSubmatch(trimmed); m != nil {
			flushPhase()
			num := 0
			fmt.Sscanf(m[1], "%d", &num)
			currentPhase = &Phase{
				Number: num,
				Title:  strings.TrimSpace(m[2]),
			}
			continue
		}

		// Verification line (must be inside a phase, before tasks)
		if currentPhase != nil && currentTask == nil {
			if m := reVerif.FindStringSubmatch(trimmed); m != nil {
				currentPhase.Verification = strings.TrimSpace(m[1])
				continue
			}
		}

		// H3: Task
		if m := reH3.FindStringSubmatch(trimmed); m != nil {
			flushTask()
			if currentPhase == nil {
				return nil, fmt.Errorf("task %s found outside of a phase", m[1])
			}
			currentTask = &Task{
				Number: m[1],
				Title:  strings.TrimSpace(m[2]),
			}
			descLines = nil
			continue
		}

		// Accumulate description lines for current task
		if currentTask != nil {
			descLines = append(descLines, line)
		}
	}

	flushPhase()

	return rm, nil
}

// Validate checks that the roadmap is well-formed.
func Validate(rm *Roadmap) []string {
	var errs []string

	if rm.ProjectName == "" {
		errs = append(errs, "missing roadmap title (expected '# Roadmap: <name>')")
	}

	if len(rm.Phases) == 0 {
		errs = append(errs, "roadmap has no phases")
		return errs
	}

	for i, phase := range rm.Phases {
		expectedNum := i + 1
		if phase.Number != expectedNum {
			errs = append(errs, fmt.Sprintf("phase %d has number %d (expected %d)", i+1, phase.Number, expectedNum))
		}
		if phase.Title == "" {
			errs = append(errs, fmt.Sprintf("phase %d has empty title", phase.Number))
		}
		if len(phase.Tasks) == 0 {
			errs = append(errs, fmt.Sprintf("phase %d has no tasks", phase.Number))
		}
		for j, task := range phase.Tasks {
			expectedTask := fmt.Sprintf("%d.%d", phase.Number, j+1)
			if task.Number != expectedTask {
				errs = append(errs, fmt.Sprintf("task %q has unexpected number (expected %s)", task.Number, expectedTask))
			}
			if task.Title == "" {
				errs = append(errs, fmt.Sprintf("task %s has empty title", task.Number))
			}
		}
	}

	return errs
}

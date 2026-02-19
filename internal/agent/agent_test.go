package agent

import (
	"strings"
	"testing"
)

func TestResolve(t *testing.T) {
	for _, name := range []string{"claude", "gemini", "codex"} {
		p, err := Resolve(name)
		if err != nil {
			t.Errorf("Resolve(%q): %v", name, err)
		}
		if p.Name != name {
			t.Errorf("got name %q, want %q", p.Name, name)
		}
	}

	_, err := Resolve("unknown")
	if err == nil {
		t.Error("expected error for unknown agent")
	}
}

func TestResolveCommand(t *testing.T) {
	p, _ := Resolve("claude")
	cmd := ResolveCommand(p, "do the thing")
	if !strings.Contains(cmd, "claude") {
		t.Errorf("command missing 'claude': %s", cmd)
	}
	if !strings.Contains(cmd, "do the thing") {
		t.Errorf("command missing prompt: %s", cmd)
	}
}

func TestResolveCommandEscaping(t *testing.T) {
	p, _ := Resolve("claude")
	cmd := ResolveCommand(p, "it's a test")
	if strings.Contains(cmd, "it's") {
		t.Errorf("single quote not escaped: %s", cmd)
	}
}

func TestBuildTaskPrompt(t *testing.T) {
	prompt := BuildTaskPrompt("1.1", "Init", "Set up the project", "/tmp/signals", "go test ./...", "")
	if !strings.Contains(prompt, "Task 1.1") {
		t.Error("prompt missing task number")
	}
	if !strings.Contains(prompt, "signal.json") {
		t.Error("prompt missing signal protocol")
	}
	if !strings.Contains(prompt, "go test") {
		t.Error("prompt missing verification")
	}
}

func TestBuildTaskPromptRetry(t *testing.T) {
	prompt := BuildTaskPrompt("1.1", "Init", "Set up", "/tmp/sig", "", "tests failed")
	if !strings.Contains(prompt, "Previous Attempt Failed") {
		t.Error("prompt missing retry section")
	}
	if !strings.Contains(prompt, "tests failed") {
		t.Error("prompt missing failure details")
	}
}

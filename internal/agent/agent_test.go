package agent

import (
	"os"
	"path/filepath"
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

func TestWriteLauncher(t *testing.T) {
	p, _ := Resolve("claude")
	dir := t.TempDir()

	promptFile := filepath.Join(dir, "prompt.md")
	os.WriteFile(promptFile, []byte("test prompt"), 0o644)

	launcherPath := filepath.Join(dir, "launch.sh")
	cmd, err := WriteLauncher(p, promptFile, launcherPath)
	if err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(cmd, "bash") {
		t.Errorf("command should use bash: %s", cmd)
	}
	if !strings.Contains(cmd, launcherPath) {
		t.Errorf("command should reference launcher: %s", cmd)
	}

	data, err := os.ReadFile(launcherPath)
	if err != nil {
		t.Fatal(err)
	}
	script := string(data)
	if !strings.Contains(script, "claude") {
		t.Errorf("script missing agent command: %s", script)
	}
	if !strings.Contains(script, promptFile) {
		t.Errorf("script missing prompt file path: %s", script)
	}
	if !strings.Contains(script, "exec") {
		t.Errorf("script should exec the agent: %s", script)
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

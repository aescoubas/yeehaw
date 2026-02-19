package tmux

import (
	"fmt"
	"os/exec"
	"strings"
)

// HasSession checks if a tmux session with the given name exists.
func HasSession(name string) bool {
	cmd := exec.Command("tmux", "has-session", "-t", name)
	return cmd.Run() == nil
}

// EnsureSession creates a new detached tmux session if it doesn't exist.
// The session starts in the given working directory.
func EnsureSession(name, workDir string) error {
	if HasSession(name) {
		return nil
	}
	cmd := exec.Command("tmux", "new-session", "-d", "-s", name, "-c", workDir)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("create session %q: %s (%w)", name, strings.TrimSpace(string(out)), err)
	}
	return nil
}

// SendText sends a string to a tmux session followed by Enter.
// This is used to execute commands in the session.
func SendText(name, text string) error {
	cmd := exec.Command("tmux", "send-keys", "-t", name, text, "Enter")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("send-keys to %q: %s (%w)", name, strings.TrimSpace(string(out)), err)
	}
	return nil
}

// CapturePane captures the visible contents of a tmux pane.
// Useful for debugging but not for completion detection.
func CapturePane(name string) (string, error) {
	cmd := exec.Command("tmux", "capture-pane", "-t", name, "-p", "-S", "-100")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("capture-pane %q: %s (%w)", name, strings.TrimSpace(string(out)), err)
	}
	return string(out), nil
}

// KillSession terminates a tmux session.
func KillSession(name string) error {
	cmd := exec.Command("tmux", "kill-session", "-t", name)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("kill-session %q: %s (%w)", name, strings.TrimSpace(string(out)), err)
	}
	return nil
}

// SessionName returns the standard tmux session name for a task.
func SessionName(taskID int64) string {
	return fmt.Sprintf("yeehaw-task-%d", taskID)
}

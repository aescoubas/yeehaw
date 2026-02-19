package agent

import (
	"fmt"
	"strings"
	"time"
)

// Profile defines how to invoke a coding agent.
type Profile struct {
	Name           string
	Command        string
	PromptFlag     string // flag to pass prompt as CLI argument
	RequiresTmux   bool
	TimeoutMinutes int
}

// DefaultProfiles returns the built-in agent profiles.
func DefaultProfiles() map[string]Profile {
	return map[string]Profile{
		"claude": {
			Name:           "claude",
			Command:        "claude",
			PromptFlag:     "--dangerously-skip-permissions -p",
			RequiresTmux:   true,
			TimeoutMinutes: 60,
		},
		"gemini": {
			Name:           "gemini",
			Command:        "gemini",
			PromptFlag:     "-p",
			RequiresTmux:   true,
			TimeoutMinutes: 60,
		},
		"codex": {
			Name:           "codex",
			Command:        "codex",
			PromptFlag:     "--prompt",
			RequiresTmux:   true,
			TimeoutMinutes: 60,
		},
	}
}

// Resolve returns the profile for the given agent name, or an error if unknown.
func Resolve(name string) (Profile, error) {
	profiles := DefaultProfiles()
	p, ok := profiles[strings.ToLower(name)]
	if !ok {
		return Profile{}, fmt.Errorf("unknown agent %q (available: claude, gemini, codex)", name)
	}
	return p, nil
}

// ResolveCommand builds the full CLI command string to launch an agent with
// the given prompt. Returns the command to be sent to a tmux session.
func ResolveCommand(profile Profile, prompt string) string {
	// Escape single quotes in the prompt for shell safety
	escaped := strings.ReplaceAll(prompt, "'", "'\\''")
	return fmt.Sprintf("%s %s '%s'", profile.Command, profile.PromptFlag, escaped)
}

// Timeout returns the agent's timeout as a time.Duration.
func (p Profile) Timeout() time.Duration {
	return time.Duration(p.TimeoutMinutes) * time.Minute
}

// BuildTaskPrompt constructs the prompt sent to an agent for a given task.
func BuildTaskPrompt(taskNumber, taskTitle, taskDescription, signalDir, verificationCmd string, previousFailure string) string {
	var b strings.Builder

	fmt.Fprintf(&b, "# Task %s: %s\n\n", taskNumber, taskTitle)
	fmt.Fprintf(&b, "%s\n\n", taskDescription)

	fmt.Fprintf(&b, "## Signal Protocol\n\n")
	fmt.Fprintf(&b, "When you are COMPLETELY done with this task, write a JSON file to:\n")
	fmt.Fprintf(&b, "  %s/signal.json\n\n", signalDir)
	fmt.Fprintf(&b, "Signal format:\n")
	fmt.Fprintf(&b, "```json\n")
	fmt.Fprintf(&b, "{\n")
	fmt.Fprintf(&b, "  \"task_id\": \"%s\",\n", taskNumber)
	fmt.Fprintf(&b, "  \"status\": \"done\",\n")
	fmt.Fprintf(&b, "  \"summary\": \"Brief description of what you did\",\n")
	fmt.Fprintf(&b, "  \"artifacts\": [\"list\", \"of\", \"files\", \"changed\"],\n")
	fmt.Fprintf(&b, "  \"timestamp\": \"RFC3339 timestamp\"\n")
	fmt.Fprintf(&b, "}\n")
	fmt.Fprintf(&b, "```\n\n")
	fmt.Fprintf(&b, "Use status \"done\" for success, \"failed\" if you cannot complete the task, or \"blocked\" if blocked.\n\n")

	if verificationCmd != "" {
		fmt.Fprintf(&b, "## Verification\n\n")
		fmt.Fprintf(&b, "After completing your work, run this verification command and include the result in your signal summary:\n")
		fmt.Fprintf(&b, "```\n%s\n```\n\n", verificationCmd)
	}

	if previousFailure != "" {
		fmt.Fprintf(&b, "## Previous Attempt Failed\n\n")
		fmt.Fprintf(&b, "This is a retry. The previous attempt failed with:\n")
		fmt.Fprintf(&b, "```\n%s\n```\n\n", previousFailure)
		fmt.Fprintf(&b, "Please fix the issues and try again.\n\n")
	}

	fmt.Fprintf(&b, "## Important\n\n")
	fmt.Fprintf(&b, "- Make commits as you work (prefix with [task-%s])\n", taskNumber)
	fmt.Fprintf(&b, "- Do NOT push to remote\n")
	fmt.Fprintf(&b, "- Writing the signal file is REQUIRED - it's how the harness knows you're done\n")

	return b.String()
}

package git

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// RepoInfo contains metadata about the current git repository.
type RepoInfo struct {
	Root       string
	MainBranch string
	HeadSHA    string
}

// DetectRepo detects the git repository at the given path.
func DetectRepo(path string) (*RepoInfo, error) {
	root, err := runGit(path, "rev-parse", "--show-toplevel")
	if err != nil {
		return nil, fmt.Errorf("not a git repo at %s: %w", path, err)
	}

	sha, err := runGit(path, "rev-parse", "HEAD")
	if err != nil {
		return nil, fmt.Errorf("get HEAD sha: %w", err)
	}

	main := detectMainBranch(path)

	return &RepoInfo{
		Root:       strings.TrimSpace(root),
		MainBranch: main,
		HeadSHA:    strings.TrimSpace(sha),
	}, nil
}

// PrepareWorktree creates a git worktree for a task. The worktree is placed
// under .yeehaw/worktrees/<branch> in the repo root. Returns the absolute
// worktree path and the base SHA it branched from.
//
// On retry, if the branch or worktree already exists, it cleans up the stale
// state before creating fresh ones.
func PrepareWorktree(repoRoot, branch string) (worktreePath, baseSHA string, err error) {
	worktreeDir := filepath.Join(repoRoot, ".yeehaw", "worktrees")
	if err := os.MkdirAll(worktreeDir, 0o755); err != nil {
		return "", "", fmt.Errorf("create worktree dir: %w", err)
	}

	worktreePath = filepath.Join(worktreeDir, branch)

	sha, err := runGit(repoRoot, "rev-parse", "HEAD")
	if err != nil {
		return "", "", fmt.Errorf("get base sha: %w", err)
	}
	baseSHA = strings.TrimSpace(sha)

	// Clean up stale worktree/branch from a previous attempt
	if _, statErr := os.Stat(worktreePath); statErr == nil {
		_, _ = runGit(repoRoot, "worktree", "remove", "--force", worktreePath)
		_, _ = runGit(repoRoot, "worktree", "prune")
	}
	if _, verifyErr := runGit(repoRoot, "rev-parse", "--verify", branch); verifyErr == nil {
		_, _ = runGit(repoRoot, "branch", "-D", branch)
	}

	_, err = runGit(repoRoot, "worktree", "add", "-b", branch, worktreePath, "HEAD")
	if err != nil {
		return "", "", fmt.Errorf("create worktree %s: %w", branch, err)
	}

	return worktreePath, baseSHA, nil
}

// CleanupWorktree removes a git worktree and prunes.
func CleanupWorktree(repoRoot, worktreePath string) error {
	if _, err := runGit(repoRoot, "worktree", "remove", "--force", worktreePath); err != nil {
		return fmt.Errorf("remove worktree: %w", err)
	}
	_, _ = runGit(repoRoot, "worktree", "prune")
	return nil
}

// BranchForTask generates a branch name from a task number and title.
func BranchForTask(taskNumber, taskTitle string) string {
	safe := strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' {
			return r
		}
		if r == ' ' {
			return '-'
		}
		return -1
	}, taskTitle)

	// Lowercase and truncate
	safe = strings.ToLower(safe)
	if len(safe) > 40 {
		safe = safe[:40]
	}
	safe = strings.TrimRight(safe, "-")

	return fmt.Sprintf("yeehaw/task-%s-%s", taskNumber, safe)
}

func detectMainBranch(path string) string {
	for _, name := range []string{"main", "master"} {
		if _, err := runGit(path, "rev-parse", "--verify", name); err == nil {
			return name
		}
	}
	return "main"
}

func runGit(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git %s: %s (%w)", strings.Join(args, " "), strings.TrimSpace(string(out)), err)
	}
	return string(out), nil
}

package git

import (
	"testing"
)

func TestBranchForTask(t *testing.T) {
	tests := []struct {
		number string
		title  string
		want   string
	}{
		{"1.1", "Init project", "yeehaw/task-1.1-init-project"},
		{"2.3", "Add user model", "yeehaw/task-2.3-add-user-model"},
		{"1.1", "Fix the bug!", "yeehaw/task-1.1-fix-the-bug"},
		{"1.1", "A very long title that exceeds the maximum allowed length for branch names in git", "yeehaw/task-1.1-a-very-long-title-that-exceeds-the-maxim"},
	}

	for _, tt := range tests {
		got := BranchForTask(tt.number, tt.title)
		if got != tt.want {
			t.Errorf("BranchForTask(%q, %q) = %q, want %q", tt.number, tt.title, got, tt.want)
		}
	}
}

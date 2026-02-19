package main

import (
	"fmt"
	"os"
	"os/exec"

	"github.com/spf13/cobra"

	"github.com/aescoubas/yeehaw/internal/agent"
	"github.com/aescoubas/yeehaw/internal/roadmap"
	"github.com/aescoubas/yeehaw/internal/tmux"
)

var roadmapCmd = &cobra.Command{
	Use:   "roadmap",
	Short: "Manage roadmaps",
}

var roadmapCreateProject string
var roadmapCreateAgent string

var roadmapCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Create a roadmap using a master agent",
	Long:  "Launch a master agent (default: claude) in a tmux session to generate a roadmap from your instructions.",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		project, err := db.GetProject(roadmapCreateProject)
		if err != nil {
			return err
		}

		agentName := roadmapCreateAgent
		profile, err := agent.Resolve(agentName)
		if err != nil {
			return err
		}

		// Build prompt for roadmap generation
		prompt := fmt.Sprintf(`You are the master planning agent for the yeehaw orchestration system.

Your job is to create a structured markdown roadmap for the project "%s" (root: %s).

Ask the user what they want to build or change, then produce a roadmap in this exact format:

# Roadmap: %s

## Phase 1: <title>
**Verification:** `+"`<command>`"+`

### Task 1.1: <title>
<description>

### Task 1.2: <title>
<description>

## Phase 2: <title>
...

Rules:
- Number phases sequentially starting from 1
- Number tasks as <phase>.<seq> (e.g., 1.1, 1.2, 2.1)
- Each phase needs a verification command
- Task descriptions should be self-contained (agents see only their task)
- Keep tasks focused (30-60 min of work each)

When the roadmap is complete, save it to: %s/.yeehaw/roadmap-draft.md
`,
			project.Name, project.RootPath, project.Name, project.RootPath)

		sessName := fmt.Sprintf("yeehaw-master-%s", project.Name)

		// Check if tmux is available
		if _, err := exec.LookPath("tmux"); err != nil {
			return fmt.Errorf("tmux not found in PATH - required for agent sessions")
		}

		if err := tmux.EnsureSession(sessName, project.RootPath); err != nil {
			return err
		}

		agentCmd := agent.ResolveCommand(profile, prompt)
		if err := tmux.SendText(sessName, agentCmd); err != nil {
			return err
		}

		fmt.Printf("Master agent (%s) launched in tmux session: %s\n", agentName, sessName)
		fmt.Printf("Attach with: tmux attach -t %s\n", sessName)
		fmt.Printf("\nAfter the agent generates the roadmap, import it with:\n")
		fmt.Printf("  yeehaw roadmap show --project %s\n", project.Name)
		return nil
	},
}

var roadmapShowProject string

var roadmapShowCmd = &cobra.Command{
	Use:   "show",
	Short: "Show the current roadmap for a project",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		project, err := db.GetProject(roadmapShowProject)
		if err != nil {
			return err
		}

		rm, err := db.GetLatestRoadmap(project.ID)
		if err != nil {
			return err
		}

		if rm == nil {
			// Try loading from draft file
			draftPath := project.RootPath + "/.yeehaw/roadmap-draft.md"
			data, err := os.ReadFile(draftPath)
			if err != nil {
				fmt.Println("No roadmap found. Create one with: yeehaw roadmap create")
				return nil
			}

			// Parse and validate
			parsed, err := roadmap.Parse(string(data))
			if err != nil {
				return fmt.Errorf("parse draft roadmap: %w", err)
			}
			errs := roadmap.Validate(parsed)

			// Store it
			status := "draft"
			if len(errs) > 0 {
				status = "invalid"
			}
			rmID, err := db.InsertRoadmap(project.ID, string(data), status)
			if err != nil {
				return err
			}

			if len(errs) > 0 {
				fmt.Println("Roadmap has validation errors:")
				for _, e := range errs {
					fmt.Printf("  - %s\n", e)
				}
				return nil
			}

			// Store phases and tasks
			for _, phase := range parsed.Phases {
				phID, err := db.InsertPhase(rmID, phase.Number, phase.Title, phase.Verification)
				if err != nil {
					return err
				}
				for _, task := range phase.Tasks {
					if _, err := db.InsertTask(phID, task.Number, task.Title, task.Description); err != nil {
						return err
					}
				}
			}

			fmt.Printf("Roadmap imported from draft (status: %s)\n\n", status)
			fmt.Print(string(data))
			return nil
		}

		fmt.Printf("Roadmap (status: %s, created: %s)\n\n", rm.Status, rm.CreatedAt)
		fmt.Print(rm.RawText)
		return nil
	},
}

var roadmapApproveProject string

var roadmapApproveCmd = &cobra.Command{
	Use:   "approve",
	Short: "Approve a draft roadmap for execution",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		project, err := db.GetProject(roadmapApproveProject)
		if err != nil {
			return err
		}

		rm, err := db.GetLatestRoadmap(project.ID)
		if err != nil {
			return err
		}
		if rm == nil {
			return fmt.Errorf("no roadmap found for project %q", project.Name)
		}
		if rm.Status != "draft" {
			return fmt.Errorf("roadmap status is %q, expected 'draft'", rm.Status)
		}

		if err := db.UpdateRoadmapStatus(rm.ID, "approved"); err != nil {
			return err
		}

		fmt.Printf("Roadmap approved for project %q. Start execution with: yeehaw run --project %s\n", project.Name, project.Name)
		return nil
	},
}

func init() {
	roadmapCreateCmd.Flags().StringVar(&roadmapCreateProject, "project", "", "project name")
	roadmapCreateCmd.Flags().StringVar(&roadmapCreateAgent, "agent", "claude", "agent to use for roadmap generation")

	roadmapShowCmd.Flags().StringVar(&roadmapShowProject, "project", "", "project name")

	roadmapApproveCmd.Flags().StringVar(&roadmapApproveProject, "project", "", "project name")

	roadmapCmd.AddCommand(roadmapCreateCmd)
	roadmapCmd.AddCommand(roadmapShowCmd)
	roadmapCmd.AddCommand(roadmapApproveCmd)
}

package main

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/aescoubas/yeehaw/internal/store"
)

var statusProject string

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show current status of projects and tasks",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		if statusProject != "" {
			return showProjectStatus(db, statusProject)
		}

		// Show all projects
		projects, err := db.ListProjects()
		if err != nil {
			return err
		}
		if len(projects) == 0 {
			fmt.Println("No projects registered.")
			return nil
		}

		for _, p := range projects {
			if err := showProjectStatus(db, p.Name); err != nil {
				fmt.Printf("  Error: %v\n", err)
			}
			fmt.Println()
		}

		cfg, err := db.GetSchedulerConfig()
		if err == nil {
			globalRunning, _ := db.CountRunningTasks()
			fmt.Printf("Global: %d/%d running tasks (max per project: %d, timeout: %dm)\n",
				globalRunning, cfg.MaxGlobal, cfg.MaxPerProject, cfg.TimeoutMinutes)
		}

		return nil
	},
}

func showProjectStatus(db *store.DB, name string) error {
	p, err := db.GetProject(name)
	if err != nil {
		return err
	}

	fmt.Printf("Project: %s (%s)\n", p.Name, p.RootPath)

	rm, err := db.GetLatestRoadmap(p.ID)
	if err != nil {
		return fmt.Errorf("  roadmap error: %w", err)
	}
	if rm == nil {
		fmt.Println("  No roadmap")
		return nil
	}

	fmt.Printf("  Roadmap: status=%s, created=%s\n", rm.Status, rm.CreatedAt)

	queued, err := db.GetQueuedTasks(rm.ID)
	if err == nil {
		fmt.Printf("  Queued tasks: %d\n", len(queued))
	}

	running, err := db.GetRunningTasks(rm.ID)
	if err == nil {
		fmt.Printf("  Running tasks: %d\n", len(running))
		for _, t := range running {
			fmt.Printf("    #%d [%s] %s (agent: %s)\n", t.ID, t.Status, t.Title, t.Agent)
		}
	}

	return nil
}

func init() {
	statusCmd.Flags().StringVar(&statusProject, "project", "", "show status for specific project")
}

package main

import (
	"fmt"
	"log"
	"os"

	"github.com/spf13/cobra"

	"github.com/aescoubas/yeehaw/internal/orchestrator"
)

var runProject string

var runCmd = &cobra.Command{
	Use:   "run",
	Short: "Run the orchestrator for a project",
	Long:  "Start the orchestration loop to dispatch and monitor agent tasks for an approved roadmap.",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		project, err := db.GetProject(runProject)
		if err != nil {
			return err
		}

		rm, err := db.GetLatestRoadmap(project.ID)
		if err != nil {
			return err
		}
		if rm == nil {
			return fmt.Errorf("no roadmap for project %q", project.Name)
		}
		if rm.Status != "approved" && rm.Status != "executing" {
			return fmt.Errorf("roadmap status is %q (need 'approved' or 'executing')", rm.Status)
		}

		if rm.Status == "approved" {
			if err := db.UpdateRoadmapStatus(rm.ID, "executing"); err != nil {
				return err
			}
		}

		logger := log.New(os.Stdout, "[yeehaw] ", log.LstdFlags)

		orch, err := orchestrator.New(db, project, rm, logger)
		if err != nil {
			return err
		}

		fmt.Printf("Orchestrator started for project %q (roadmap #%d)\n", project.Name, rm.ID)
		fmt.Println("Press Ctrl+C to stop.")

		return orch.RunForever()
	},
}

func init() {
	runCmd.Flags().StringVar(&runProject, "project", "", "project name")
}

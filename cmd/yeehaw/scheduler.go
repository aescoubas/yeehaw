package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

var schedulerCmd = &cobra.Command{
	Use:   "scheduler",
	Short: "Manage scheduler configuration",
}

var schedMaxGlobal int
var schedMaxProject int
var schedTimeout int

var schedulerConfigCmd = &cobra.Command{
	Use:   "config",
	Short: "View or update scheduler limits",
	Long:  "Without flags, shows current config. With flags, updates the specified values.",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()

		// Check if any flags were explicitly set
		globalSet := cmd.Flags().Changed("max-global")
		projectSet := cmd.Flags().Changed("max-project")
		timeoutSet := cmd.Flags().Changed("timeout")

		if globalSet || projectSet || timeoutSet {
			var gp, pp, tp *int
			if globalSet {
				gp = &schedMaxGlobal
			}
			if projectSet {
				pp = &schedMaxProject
			}
			if timeoutSet {
				tp = &schedTimeout
			}
			if err := db.UpdateSchedulerConfig(gp, pp, tp); err != nil {
				return err
			}
			fmt.Println("Scheduler config updated.")
		}

		cfg, err := db.GetSchedulerConfig()
		if err != nil {
			return err
		}

		fmt.Printf("Scheduler Configuration:\n")
		fmt.Printf("  Max global tasks:      %d\n", cfg.MaxGlobal)
		fmt.Printf("  Max per-project tasks: %d\n", cfg.MaxPerProject)
		fmt.Printf("  Timeout (minutes):     %d\n", cfg.TimeoutMinutes)
		return nil
	},
}

func init() {
	schedulerConfigCmd.Flags().IntVar(&schedMaxGlobal, "max-global", 5, "maximum concurrent tasks globally")
	schedulerConfigCmd.Flags().IntVar(&schedMaxProject, "max-project", 3, "maximum concurrent tasks per project")
	schedulerConfigCmd.Flags().IntVar(&schedTimeout, "timeout", 60, "task timeout in minutes")

	schedulerCmd.AddCommand(schedulerConfigCmd)
}

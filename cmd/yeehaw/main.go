package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/aescoubas/yeehaw/internal/store"
)

func main() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

var dbPath string

var rootCmd = &cobra.Command{
	Use:   "yeehaw",
	Short: "Yeehaw - multi-agent coding orchestrator",
	Long:  "Orchestrate multiple coding agents (Claude Code, Gemini CLI, Codex) to work on software projects autonomously.",
}

func init() {
	rootCmd.PersistentFlags().StringVar(&dbPath, "db", store.DefaultPath(), "path to SQLite database")

	rootCmd.AddCommand(initDBCmd)
	rootCmd.AddCommand(projectCmd)
	rootCmd.AddCommand(roadmapCmd)
	rootCmd.AddCommand(runCmd)
	rootCmd.AddCommand(statusCmd)
	rootCmd.AddCommand(schedulerCmd)
	rootCmd.AddCommand(tuiCmd)
}

func openDB() (*store.DB, error) {
	return store.Open(dbPath)
}

func mustOpenDB() *store.DB {
	db, err := openDB()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error opening database: %v\n", err)
		os.Exit(1)
	}
	if err := db.InitDB(); err != nil {
		fmt.Fprintf(os.Stderr, "Error initializing database: %v\n", err)
		os.Exit(1)
	}
	return db
}

// init-db
var initDBCmd = &cobra.Command{
	Use:   "init-db",
	Short: "Initialize the SQLite database",
	RunE: func(cmd *cobra.Command, args []string) error {
		db, err := openDB()
		if err != nil {
			return err
		}
		defer db.Close()
		if err := db.InitDB(); err != nil {
			return err
		}
		fmt.Printf("Database initialized at %s\n", dbPath)
		return nil
	},
}

// --- project ---

var projectCmd = &cobra.Command{
	Use:   "project",
	Short: "Manage projects",
}

var projectAddName string
var projectAddRoot string

var projectAddCmd = &cobra.Command{
	Use:   "add",
	Short: "Register a new project",
	RunE: func(cmd *cobra.Command, args []string) error {
		if projectAddName == "" || projectAddRoot == "" {
			return fmt.Errorf("--name and --root are required")
		}
		db := mustOpenDB()
		defer db.Close()
		p, err := db.AddProject(projectAddName, projectAddRoot)
		if err != nil {
			return err
		}
		fmt.Printf("Project %q added (id=%d, root=%s)\n", p.Name, p.ID, p.RootPath)
		return nil
	},
}

var projectListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all projects",
	RunE: func(cmd *cobra.Command, args []string) error {
		db := mustOpenDB()
		defer db.Close()
		projects, err := db.ListProjects()
		if err != nil {
			return err
		}
		if len(projects) == 0 {
			fmt.Println("No projects registered.")
			return nil
		}
		for _, p := range projects {
			fmt.Printf("  %-20s %s\n", p.Name, p.RootPath)
		}
		return nil
	},
}

func init() {
	projectAddCmd.Flags().StringVar(&projectAddName, "name", "", "project name")
	projectAddCmd.Flags().StringVar(&projectAddRoot, "root", "", "project root path")
	projectCmd.AddCommand(projectAddCmd)
	projectCmd.AddCommand(projectListCmd)
}

import { createContext } from "react";

export interface DashboardProject {
  id: string;
  slug: string;
  name: string;
  /** The isolated Hermes runtime owned by this project. */
  profile: string;
}

export interface ProjectContextValue {
  projectId: string;
  projects: DashboardProject[];
  loading: boolean;
  setProjectId: (projectId: string) => void;
}

export const ProjectContext = createContext<ProjectContextValue>({
  projectId: "",
  projects: [],
  loading: true,
  setProjectId: () => {},
});

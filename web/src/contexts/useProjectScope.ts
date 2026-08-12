import { useContext } from "react";
import { ProjectContext } from "@/contexts/project-context";

export function useProjectScope() {
  return useContext(ProjectContext);
}

import { BriefcaseBusiness } from "lucide-react";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { cn } from "@/lib/utils";
import { useProjectScope } from "@/contexts/useProjectScope";

/** Visible on every core dashboard route, above profile administration. */
export function ProjectSwitcher({ collapsed }: { collapsed?: boolean }) {
  const { projectId, projects, loading, setProjectId } = useProjectScope();
  if (!loading && projects.length === 0) return null;
  return (
    <div className={cn("flex items-center gap-2 border-b border-current/10 px-3 py-2", collapsed && "lg:justify-center lg:px-0")}>
      <BriefcaseBusiness className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
      <Select
        id="datansh-project-switcher"
        aria-label="Project"
        value={projectId}
        onValueChange={setProjectId}
        disabled={loading || projects.length === 0}
        className={cn(
          "min-w-0 flex-1",
          collapsed && "lg:hidden",
          "[&_button]:h-7 [&_button]:border-border [&_button]:bg-background [&_button]:px-2 [&_button]:text-xs",
          "[&_button]:font-sans [&_button]:normal-case [&_button]:tracking-normal",
          "[&_[role=listbox]>div]:font-sans [&_[role=listbox]>div]:text-xs",
        )}
      >
        {loading ? <SelectOption value="">Loading projects…</SelectOption> : null}
        {projects.map((project) => <SelectOption key={project.id} value={project.id}>{project.name}</SelectOption>)}
      </Select>
      {collapsed && <span className="sr-only">Project</span>}
    </div>
  );
}
